import serial
import serial.tools.list_ports
import time
import threading as thread

class LaserCommandError(Exception):
    pass

class LaserStatusResponse():
    def __init__(self, response):
        """Parses the response string into a new LaserStatusResponseObject"""

        i = int(response) # slice off the \r at the end
        self.laser_enabled = bool(i & 1)
        self.laser_active = bool(i & 2)
        self.diode_external_trigger = bool(i & 8)
        self.external_interlock = bool(i & 64)
        self.resonator_over_temp = bool(i & 128)
        self.electrical_over_temp = bool(i & 256)
        self.power_failure = bool(i & 512)
        self.ready_to_enable = bool(i & 1024)
        self.ready_to_fire = bool(i & 2048)
        self.low_power_mode = bool(i & 4096)
        self.high_power_mode = bool(i & 8192)

    def __str__(self):
        """Returns a string representation of the laser status. Should be an ASCII number as shown in the user manual."""
        i = 0
        if self.laser_enabled:
            i += 1
        if self.laser_active:
            i += 2
        if self.diode_external_trigger:
            i += 8
        if self.external_interlock:
            i += 64
        if self.resonator_over_temp:
            i += 128
        if self.electrical_over_temp:
            i += 256
        if self.power_failure:
            i += 512
        if self.ready_to_enable:
            i += 1024
        if self.ready_to_fire:
            i += 2048
        if self.low_power_mode:
            i += 4096
        if self.high_power_mode:
            i += 8192

        return str(i)

class Laser:
    # Constants for Energy Mode
    MANUAL_ENERGY = 0
    LOW_ENERGY = 1
    HIGH_ENERGY = 2

    # Constants for shot mode
    CONTINUOUS = 0
    SINGLE_SHOT = 1
    BURST = 2

    def __init__(self, pulseMode = 0, pulsePeriod = 0, repRate = 1, burstCount = 10, diodeCurrent = .1, energyMode = 0, pulseWidth = 10, diodeTrigger = 0):
        self._ser = None
        self.pulseMode = pulseMode # NOTE: Pulse mode 0 = continuous is actually implemented as 2 = burst mode in this code.
        self.pulsePeriod = pulsePeriod
        self.repRate = repRate          # NOTE: The default repitition rate for the laser is 1 Hz not 10 Hz (10 is out of bounds aswell)
        self.burstCount = burstCount    # NOTE: The default burst count for the laser is 10 not 10000
        self.diodeCurrent = diodeCurrent
        self.energyMode = energyMode
        self.pulseWidth = pulseWidth
        self.diodeTrigger = diodeTrigger
        self.burstDuration = burstCount/repRate

        self._kicker_control = False  # False = off, True = On. Controls kicker for shots longer than 2 seconds
        self._startup = True
        self._threads = []
        self._lock = thread.Lock() # this lock will be acquired every time the serial port is accessed.
        self._device_address = "LA"
        self.connected = False

    def editConstants(self, pulseMode = 0, pulsePeriod = 0, repRate = 1, burstCount = 10, diodeCurrent = .1, energyMode = 0, pulseWidth = 10,  diodeTrigger = 0):
        """
        Update the laser settings

        Parameters
        ----------
        pulseMode : int
            0 = continuous (actually implemented as burst mode), 1 = single shot, 2 = burst mode in this code.
        repRate : float
            0 = Rate of laser firing in Hz (1 to 30)
        burstCount : int
            number of shots to fire (if pulse mode is 0 or 2)
        diodeCurrent : float
            Current to diode (see laser ATP spec sheet which comes with the laser for  optimal settings)
        energyMode : int
            0 = manual mode, 1 = low power, 2 = high power
        pulseWidth : float
            sets the diode pulse width (see laser ATP spec sheet which comes with laser for optimal settigns)
        diodeTrigger : int
            0 = internal, 1 = external
        """
        self.pulseMode = pulseMode
        self.pulsePeriod = pulsePeriod
        self.repRate = repRate
        self.burstCount = burstCount
        self.diodeCurrent = diodeCurrent
        self.energyMode = energyMode
        self.pulseWidth = pulseWidth
        self.diodeTrigger = diodeTrigger
        self.burstDuration = burstCount/repRate
        self.update_settings()

    def _kicker(self):  # queries for status every second in order to kick the laser's WDT on shots >= 2s
        """Queries for status every second in order to kick the laser's WDT on shots >= 2s"""
        while True:
            if self._kicker_control:
                self._ser.write('SS?')
            time.sleep(1)

    def _send_command(self, cmd):
        """
        Sends command to laser

        Parameters
        ----------
        cmd : string
            This contains the ASCII of the command to be sent. Should not include the prefix, address, delimiter, or terminator

        Returns
        ----------
        response : bytes
            The binary response received by the laser. Includes the '\r' terminator. May be None is the read timedout.
        """
        if len(cmd) == 0:
            return

        if not self.connected:
            raise ConnectionError("Not connected to a serial port. Please call connect() before issuing any commands!")

        # Form the complete command string, in order this is: prefix, address, delimiter, command, and terminator
        cmd_complete = ";" + self._device_address + ":" + cmd + "\r"

        with self._lock: # make sure we're the only ones on the serial line
            self._ser.write(cmd_complete.encode("ascii")) # write the complete command to the serial device
            time.sleep(0.01)
            response = self._ser.read_until("\r") # laser returns with <CR> = \r Note that this may timeout and return None

        return response

    def connect(self, port_number, baud_rate=115200, timeout=1, parity=None):
        """
        Sets up connection between flight computer and laser

        Parameters
        ----------
        port_number : int
            This is the port number for the laser

        baud_rate : int
            Bits per second on serial connection

        timeout : int
            Number of seconds until a read operation fails.

        """
        with self._lock:
            if port_number not in serial.tools.list_ports.comports():
                raise ValueError(f"Error: port {port_number} is not available")
            self._ser = serial.Serial(port=port_number)
            if baud_rate and isinstance(baud_rate, int):
                self._ser.baudrate = baud_rate
            else:
                raise ValueError('Error: baud_rate parameter must be an integer')
            if timeout and isinstance(timeout, int):
                self._ser.timeout = timeout
            else:
                raise ValueError('Error: timeout parameter must be an integer')
            if not parity or parity == 'none':
                self._ser.parity = serial.PARITY_NONE
            elif parity == 'even':
                self._ser.parity = serial.PARITY_EVEN
            elif parity == 'odd':
                self._ser.parity = serial.PARITY_ODD
            elif parity == 'mark':
                self._ser.parity = serial.PARITY_MARK
            elif parity == 'space':
                self._ser.parity = serial.PARITY_SPACE
            else:
                raise ValueError("Error: parity must be None, \'none\', \'even\', \'odd\', \'mark\', \'space\'")
            if self._startup:  # start kicking the laser's WDT
                t = thread.Thread(target=self._kicker())
                self._threads.append(t)
                t.start()
                self._startup = False

    def fire_laser(self):
        """
            Sends commands to laser to have it fire
        """
        fire_response = self._send_command('FL 1')
        if fire_response != b"OK\r":
            raise LaserCommandError(Laser.get_error_code_description(fire_response))
        #TODO: Add in command to check status
        status = self.get_status()
        if self.energyMode == self.LOW_ENERGY:      # Make sure we are NOT in low energy mode... Laser must either be in high energy or manual to fire
            raise LaserCommandError("Laser is in Low Energy Mode")

        if not self.check_armed():
            raise LaserCommandError("Laser not armed")

        if status != '3075' and status != '11267':  # TODO: This seems wrong. Check to make sure that this is the EXACT status string that will be returned during firing
                                                    # TODO: I believe that the laser on the manual is stuck in manual power mode. Therefore, in specifically high power mode it could also be 11267
            self._send_command('FL 0')  # aborts if laser fails to fire
            raise LaserCommandError('Laser Failed to Fire')
        else:
            #TODO: Now that I'm viewing this, if we are to call these sleep timers, there is no way to perform an emergency stop while firing. These need threaded or something.
            if self.pulseMode == 0:
                time.sleep(self.pulsePeriod)            # Full pulse period
            elif self.pulseMode == 1:
                time.sleep(1 / self.repRate)            # Only active for the 1s / repitition rate (pulse width)
            elif self.pulseMode == 2:
                # TODO: Add better kicker functionality (allow for it to be stopped instead of running an infinite loop | This should also be threaded as to not hang on an inf loop)
                if self.burstDuration >= 2:
                    self._kicker_control = True         # Burst firing
                    time.sleep(self.burstDuration)      # If duration >= 2, trigger kicker to check status and sleep for duration
                    self._kicker_control = False
                else:
                    time.sleep(self.burstDuration)      # Just sleep for duration otherwise

            self._send_command('FL 0')

    def get_status(self): # TODO: Make this return useful values to the user. The user should not have to parse out the information encoded in the string you return.
        """
        Obtains the status of the laser
        Returns
        ______
        status : LaserStatusResponse object
                Returns a LaserStatusResponse object created from the SS? command's response that is received.
        """
        response = self._send_command('SS?')
        response = str(response[:-1].decode('ascii'))

        if response[0] == "?": # Check to see if we got an error instead. NOTE: This originally had len(response) < 5, but I don't see the purpose of this and it causes errors.
            raise LaserCommandError(Laser.get_error_code_description(response))
        else:
            return LaserStatusResponse(response)

    def check_armed(self):
        """
        Checks if the laser is armed
        Returns
        _______
        armed : boolean
            the lasr is armed
        """
        response = self._send_command('EN?')
        if response[:-1] == b"?":
            raise LaserCommandError(Laser.get_error_code_description(response))

        if len(response) == 2:
            return response[:-1] == b'1'

    def fet_temp_check(self):
        """
        Checks the FET temperature the laser

        Returns
        _______
        fet : bytes
            Returns the float value of the FET temperature in bytes string.
        """
        response = self._send_command('FT?')
        if response[0] == b"?":
            raise LaserCommandError(Laser.get_error_code_description(response))
        return response[:-4]

    def resonator_temp_check(self):
        """
        Checks the resonator temperature the laser

        Returns
        _______
        resonator_temp : bytes
            Returns the float value of the resonator temperature in bytes string.
        """
        #TODO: Determine if this is a float or an integer value and return the appropriate data type.
        response = self._send_command('TR?')
        if response[0] == b"?":
            raise LaserCommandError(Laser.get_error_code_description(response))
        return response[:-4]

    def fet_voltage_check(self):
        """
        Checks the FET voltage of the laser

        Returns
        _______
        fet_voltage : bytes
            Returns the float value of the FET voltage in bytes string.
        """
        #TODO: Determine through testing if this is a float or an integer and perform the appropriate cast before returning.
        response = self._send_command('FV?')
        if response[0] == b"?":
            raise LaserCommandError(Laser.get_error_code_description(response))
        return response[:-4]    #TODO: All responce[:-4] does is returns b'', what is the purpose of this... It should be returning the actual data, something like responce [:-1] would remove the \r and leave just data

    def diode_current_check(self):
        """
        Checks current to diode of the laser

        Returns
        -------
        diode_current : bytes
            Returns the float value of the diode current in bytes string.
        """
        #TODO: Determine via testing if this is a float value or integer value, and perform the appropriate cast before returning.
        response = self._send_command('IM?')
        if response[0] == b"?":
            raise LaserCommandError(Laser.get_error_code_description(response))
        return response[:-4]

    def bank_voltage_check(self):
        """
        This command requests to see what value the laser's bank voltage is at.
        
        Returns
        -------
        bank_voltage : float
            Returns the float value of the laser's bank voltage.
        """
        #TODO: May be an int or float, has to be tested. A lot of these aren't specified on the data sheet. Once determined, cast the responce_str properly.
        #TODO: Also, I thought it'd be easier if we just returned an actual value instead of an ascii encoded string. Go ahead and change this if you'd like.
        response = self._send_command('BV?')
        if response[0] == b'?':
            raise LaserCommandError(Laser.get_error_code_description(response))
        response_str = response[:-1].decode('ascii')
        return float(response_str)

    def laser_ID_check(self):
        """
        This command requests to see what the laser's ID value is.

        Returns
        _______
        ID : str
            Returns a string containing the laser's ID information
        """
        response = self._send_command('ID?')
        if response[0] == b'?':
            raise LaserCommandError(Laser.get_error_code_description(response))
        response_str = str(response[:-1].decode('ascii'))
        return response_str

    def latched_status_check(self):
        """
        This command requests to see what the laser's latched status is.

        Returns
        _______
        latched_status : str
            Returns a string containing the laser's latched status
        """
        #TODO: Not especially sure what this returns
        response = self._send_command('LS?')
        if response[0] == b'?':
            raise LaserCommandError(Laser.get_error_code_description(response))
        response_str = str(response[:-1].decode('ascii'))
        return response_str

    def system_shot_count_check(self):
        """
        This command requests to see what the laser's system shot count is.

        Returns
        -------
        system_SC : int
            Returns the system shot count since factory build.
        """
        response = self._send_command('SC?')
        if response[0] == b'?':
            raise LaserCommandError(Laser.get_error_code_description(response))
        response_str = str(response[:-1].decode('ascii'))
        return int(response_str)

    def emergency_stop(self):
        """Immediately sends command to laser to stop firing"""
        response = self._send_command('FL 0')
        if response == b"OK\r":
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def arm(self):
        """Sends command to laser to arm. Returns True on nominal response."""
        response = self._send_command('EN 1')
        if response == b"OK\r":
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def disarm(self):
        """Sends command to laser to disarm. Returns True on nominal response."""
        response = self._send_command('EN 0')

        if response == b"OK\r":
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_pulse_mode(self, mode):
        """Sets the laser pulse mode. 0 = continuous, 1 = single shot, 2 = burst. Returns True on nominal response."""
        if not mode in (0,1,2) or not type(mode) == int:
            raise ValueError("Invalid value for pulse mode! 0, 1, or 2 are accepted values.")

        response = self._send_command("PM " + str(mode))
        if response == b"OK\r":
            self.pulseMode = mode
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_pulse_period(self, period):
        """Sets the pulse period for firing."""
        #TODO: We must find the pulse period MIN and MAX restrictions when we get the laser control box.
        #TODO: Once found, add these restrictions into the function so we don't send over invalid commands.
        #TODO: Also, we need to see what the laser's default pulse period is set to.
        response = self._send_command("PE " + str(period))
        if response == b"OK\r":
            self.pulsePeriod = float(period)
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))


    def set_diode_trigger(self, trigger):
        """Sets the diode trigger mode. 0 = Software/internal. 1 = Hardware/external trigger. Returns True on nominal response."""
        if trigger != 0 and trigger != 1 or not type(trigger) == int:
            raise ValueError("Invalid value for trigger mode! 0 or 1 are accepted values.")

        response = self._send_command("DT " + str(trigger))
        if response == b"OK\r":
            self.diodeTrigger = trigger
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_pulse_width(self, width):
        """Sets the diode pulse width. Width is in seconds, may be a float. Returns True on nominal response, False otherwise."""

        if type(width) != int and type(width) != float or width <= 0:
            raise ValueError("Pulse width must be a positive, non-zero number value (no strings)!")

        width = float(width)

        response = self._send_command("DW " + str(width))
        if response == b"OK\r":
            self.pulseWidth = width
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_burst_count(self, count):
        """Sets the burst count of the laser. Must be a positive non-zero integer. Returns True on nominal response, False otherwise."""
        if count <= 0 or not type(count) == int:
            raise ValueError("Burst count must be a positive, non-zero integer!")

        response = self._send_command("BC " + str(count))
        if response == b"OK\r":
            self.burstCount = count
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_rep_rate(self, rate):
        """Sets the repetition rate of the laser. Rate must be a positive integer from 1 to 5 (# of Hz allowed). Returns True on nominal response, False otherwise."""
        if not type(rate) == int or rate < 1 or rate > 5:
            raise ValueError("Laser repetition rate must be a positive integer from 1 to 5!")

        response = self._send_command("RR " + str(rate))
        if response == b"OK\r":
            self.repRate = rate
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_diode_current(self, current):
        """Sets the diode current of the laser. Must be a positive non-zero integer (maybe even a float?). Returns True on nominal response, False otherwise."""
        if (type(current) != int and type(current) != float) or current <= 0:
            raise ValueError("Diode current must be a positive, non-zero number!")

        response = self._send_command("DC " + str(current))
        if response == b"OK\r":
            self.diodeCurrent = current
            self.energyMode = 0 # Whenever diode current is adjusted manually, the energy mode is set to manual.
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def set_energy_mode(self, mode):
        """Sets the energy mode of the laser. 0 = manual, 1 = low power, 2 = high power. Returns True on nominal response, False otherwise."""
        if type(mode) != int:
            raise ValueError("Energy mode must be an integer!")

        if not mode in (0, 1, 2):
            raise ValueError("Valid values for energy mode are 0, 1 and 2!")

        response = self._send_command("EM " + str(mode))
        if response == b"OK\r":
            self.energyMode = mode
            return True
        raise LaserCommandError(Laser.get_error_code_description(response))

    def laser_reset(self):
        """This command resets all laser variables to default. """
        responce = self._send_command('RS')
        if responce == b'OK\r':
            self.editConstants()    # Refreshing all constants back to their default states if response is valid
            return True
        raise LaserCommandError(Laser.get_error_code_description(responce))


    def update_settings(self):
        # cmd format, ignore brackets => ;[Address]:[Command String][Parameters]\r
        """Updates laser settings"""
        cmd_strings = list()
        cmd_strings.append('RR ' + str(self.repRate))
        cmd_strings.append('BC ' + str(self.burstCount))
        cmd_strings.append('DC ' + str(self.diodeCurrent))
        cmd_strings.append('EM ' + str(self.energyMode))
        cmd_strings.append('PM ' + str(self.pulseMode))
        cmd_strings.append('DW ' + str(self.pulseWidth))
        cmd_strings.append('DT ' + str(self.pulseMode))

        for i in cmd_strings:
            self._send_command(i)

    @staticmethod
    def get_error_code_description(code):
        if code == b'?1':
            return "Command not recognized."
        elif code == b'?2':
            return "Missing command keyword."
        elif code == b'?3':
            return "Invalid command keyword."
        elif code == b'?4':
            return "Missing Parameter"
        elif code == b'?5':
            return "Invalid Parameter"
        elif code == b'?6':
            return "Query only. Command needs a question mark."
        elif code == b'?7':
            return "Invalid query. Command does not have a query function."
        elif code == b'?8':
            return "Command unavailable in current system state."
        else:
            return "Error description not found, response code given: " + str(code)

def list_available_ports():
    return serial.tools.list_ports.comports()

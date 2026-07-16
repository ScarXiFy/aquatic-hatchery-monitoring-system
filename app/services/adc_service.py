import logging
from flask import current_app

logger = logging.getLogger(__name__)

# Attempt to import CircuitPython libraries
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    CIRCUITPYTHON_AVAILABLE = True
except ImportError:
    CIRCUITPYTHON_AVAILABLE = False
    logger.warning("ADS1115 libraries not present. Running in simulated mode.")


class ADCService:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ADCService, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.ads = None
        self.i2c = None
        self.available = False
        self.channels = {}
        self._initialize_hardware()

    def _initialize_hardware(self):
        """Attempts to initialize I2C and ADS1115 hardware once."""
        if not CIRCUITPYTHON_AVAILABLE:
            self.available = False
            return

        try:
            # Try to get configured address, default to 0x48
            address = 0x48
            try:
                if current_app:
                    address = current_app.config.get("ADS1115_ADDRESS", 0x48)
            except RuntimeError:
                pass

            # Initialize I2C bus and ADS1115
            self.i2c = busio.I2C(board.SCL, board.SDA)
            self.ads = ADS.ADS1115(self.i2c, address=address)
            self.available = True
            self._initialized = True
            logger.info(f"Successfully initialized ADS1115 at address {hex(address)}")
        except Exception as e:
            logger.exception("Failed to initialize ADS1115 / I2C bus")
            self.available = False
            self.ads = None
            self.i2c = None

    def _get_analog_in_channel(self, channel_num: int):
        """Retrieves or creates AnalogIn instance for the given channel (0-3)."""
        if not self.available:
            # Attempt to re-initialize if it was previously unavailable
            self._initialize_hardware()
            if not self.available:
                raise RuntimeError("ADS1115 ADC hardware is not available")

        if channel_num in self.channels:
            return self.channels[channel_num]

        ads_channels = {
            0: 0,
            1: 1,
            2: 2,
            3: 3
        }

        if channel_num not in ads_channels:
            raise ValueError(f"Invalid ADC channel: {channel_num}. Must be 0, 1, 2, or 3.")

        try:
            analog_in = AnalogIn(self.ads, ads_channels[channel_num])
            self.channels[channel_num] = analog_in
            return analog_in
        except Exception as e:
            logger.exception(f"Failed to setup AnalogIn on channel {channel_num}")
            self.available = False
            self.channels.clear()
            raise e

    def read_filtered_voltage(self, channel_num: int) -> float:
        """
        Takes 10 voltage samples, sorts them, discards the 2 highest and 2 lowest,
        averages the remaining 6 samples, and returns the filtered voltage.
        """
        try:
            chan = self._get_analog_in_channel(channel_num)
            
            samples = []
            for _ in range(10):
                samples.append(chan.voltage)

            samples.sort()
            # Discard the 2 lowest (index 0, 1) and 2 highest (index -1, -2)
            filtered_samples = samples[2:-2]
            avg_voltage = sum(filtered_samples) / len(filtered_samples)
            return avg_voltage
        except Exception as e:
            logger.exception(f"I2C communication error or hardware failure while reading channel {channel_num}")
            self.available = False
            self.channels.clear()
            raise e

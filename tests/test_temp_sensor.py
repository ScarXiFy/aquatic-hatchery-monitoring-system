import unittest
from unittest.mock import patch, mock_open
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.temp_sensor import (
    parse_w1_slave,
    read_ds18b20,
    read_main_tank_temp,
    read_mixing_tank_temp,
    is_w1_sensor_available,
    MAIN_TANK_DEVICE_FILE,
)


class TestTempSensorService(unittest.TestCase):
    def test_parse_w1_slave_valid(self):
        sample_output = [
            "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n",
            "72 01 4b 46 7f ff 0e 10 57 t=23125\n",
        ]
        temp = parse_w1_slave(sample_output)
        self.assertEqual(temp, 23.12)

    def test_parse_w1_slave_crc_fail(self):
        sample_output = [
            "72 01 4b 46 7f ff 0e 10 57 : crc=57 NO\n",
            "72 01 4b 46 7f ff 0e 10 57 t=23125\n",
        ]
        temp = parse_w1_slave(sample_output)
        self.assertIsNone(temp)

    def test_parse_w1_slave_malformed(self):
        self.assertIsNone(parse_w1_slave([]))
        self.assertIsNone(parse_w1_slave(["only one line\n"]))
        self.assertIsNone(parse_w1_slave(["YES\n", "no temperature here\n"]))

    @patch("pathlib.Path.exists")
    def test_read_ds18b20_file_not_found(self, mock_exists):
        mock_exists.return_value = False
        self.assertIsNone(read_ds18b20("/fake/path"))

    @patch("pathlib.Path.exists")
    def test_read_ds18b20_success(self, mock_exists):
        mock_exists.return_value = True
        sample_data = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=25500\n"
        with patch("builtins.open", mock_open(read_data=sample_data)):
            temp = read_ds18b20("/sys/bus/w1/devices/28-000000b197bd/w1_slave")
            self.assertEqual(temp, 25.50)

    @patch("app.services.temp_sensor.read_ds18b20")
    def test_read_main_and_mixing_tank(self, mock_read):
        mock_read.return_value = 26.30
        self.assertEqual(read_main_tank_temp(), 26.30)
        self.assertEqual(read_mixing_tank_temp(), 26.30)


if __name__ == "__main__":
    unittest.main()

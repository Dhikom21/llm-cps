"""
real_hardware.py — drives the ACTUAL Raspberry Pi hardware.

Exposes the same public methods as fake_hardware.FakeRoom, so swapping the
edge agent from simulation to hardware is a single import change:

    import real_hardware as hw
    room = hw.get_room()

--------------------------------------------------------------------------
One-time Pi setup
--------------------------------------------------------------------------
    sudo raspi-config     # Interface Options -> enable 1-Wire  AND  I2C
    sudo reboot
    pip install gpiozero w1thermsensor adafruit-circuitpython-ads1x15

--------------------------------------------------------------------------
Wiring (see CPS_Wiring_Diagram.png)
--------------------------------------------------------------------------
    Relay (Grove):   SIG -> GPIO17 (pin 11) | VCC -> 5V | GND -> GND
    Buzzer:          GPIO27 (pin 13) -> 1k -> NPN base;
                     buzzer between 5V and NPN collector; NPN emitter -> GND
    DS18B20:         DATA -> GPIO4 (pin 7)  | VDD -> 3.3V | GND -> GND
                     + 4.7k resistor between DATA and 3.3V
    ADS1115:         SDA -> GPIO2 (pin 3)   | SCL -> GPIO3 (pin 5)
                     VDD -> 3.3V | GND -> GND
    Smoke (SEN0570): A(out) -> ADS1115 A0   | VCC -> 3.3V | GND -> GND
"""
from gpiozero import OutputDevice, Buzzer
from w1thermsensor import W1ThermSensor
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

HEATER_PIN = 17          # BCM numbering (physical pin 11)
BUZZER_PIN = 27          # BCM numbering (physical pin 13)


class RealRoom:
    def __init__(self):
        # actuators (digital outputs)
        self._heater = OutputDevice(HEATER_PIN, active_high=True, initial_value=False)
        self._buzzer = Buzzer(BUZZER_PIN)
        # temperature over 1-Wire
        self._temp = W1ThermSensor()
        # analog smoke via the ADS1115 ADC over I2C
        i2c = busio.I2C(board.SCL, board.SDA)
        self._smoke = AnalogIn(ADS.ADS1115(i2c), ADS.P0)

    # ---------- shared interface (identical to FakeRoom) ----------
    def read_temperature(self):
        return round(self._temp.get_temperature(), 2)

    def read_smoke(self):
        return round(self._smoke.voltage, 3)     # volts; higher = more smoke

    def set_heater(self, on):
        self._heater.on() if on else self._heater.off()

    def set_buzzer(self, on):
        self._buzzer.on() if on else self._buzzer.off()


def get_room():
    """Factory so the edge agent can stay driver-agnostic: hw.get_room()."""
    return RealRoom()


if __name__ == "__main__":
    import time
    r = get_room()
    print("Heater ON for 5 s ...")
    r.set_heater(True); time.sleep(5); r.set_heater(False)
    print("temp:", r.read_temperature(), " smoke:", r.read_smoke())
    print("Buzzer beep ...")
    r.set_buzzer(True); time.sleep(1); r.set_buzzer(False)

from .config import SensorConfig


class MockSensor:
    def __init__(self):
        self.present = None

    def read(self):
        return self.present

    def close(self):
        pass


class GPIOSensor:
    def __init__(self, config: SensorConfig):
        import gpiod
        from gpiod.line import Bias, Direction

        self.config = config
        self.request = gpiod.request_lines(
            config.chip,
            consumer="sonoff-mains",
            config={config.line: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)},
        )

    def read(self):
        from gpiod.line import Value

        level = 1 if self.request.get_value(self.config.line) == Value.ACTIVE else 0
        return level == self.config.present_level

    def close(self):
        self.request.release()

"""Dev-machine shim — real lgpio is installed on the Raspberry Pi only."""


def gpiochip_open(chip: int) -> int:
    raise OSError("lgpio is only available on Raspberry Pi")


def gpiochip_close(handle: int) -> None:
    pass


def gpio_claim_output(handle: int, gpio: int, level: int = 0, flags: int = 0) -> None:
    pass


def gpio_write(handle: int, gpio: int, level: int) -> None:
    pass


def gpio_free(handle: int, gpio: int) -> None:
    pass

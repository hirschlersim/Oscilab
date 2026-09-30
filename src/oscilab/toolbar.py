"""
Custom toolbar buttons and toggles, defined as methods of your own class.

    from oscilab import button, toggle

    class LabTools:
        def __init__(self, device):
            self.device = device

        @button("Zero", tooltip="Zero the sensor")
        def zero(self):
            self.device.zero()

        @toggle("Heater", tooltip="Switch the sample heater")
        def heater(self, on: bool):
            self.device.set_heater(on)

    scope.add_toolbar(LabTools(device))

They appear in the toolbar after the built-in controls (next to Downsample),
in the order they're defined, base classes first. A button's method is
called with no arguments; a toggle's with its new state (True = on) on every
click. `@classmethod`s work too — then pass the class itself.

A method that raises doesn't take the app down: the error shows in the
status bar, the traceback goes to the log file, and a toggle flips back to
the state it was in.

Producers (`oscilab.producers`) use the same decorators, but their marked
methods go in the producer's entry of the Producers menu (next to Start and
Stop) — and in the toolbar as well with `toolbar=True`:

    class ImuProducer(SyncDataProducer):
        @button("Zero", toolbar=True)
        def zero(self):
            self.device.zero()

That's one control in two places: a toggle shows the same state in both.
Either way they run on the producer's own thread between reads — so they
can use its device connection safely. A producer that isn't running
refuses them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

_MARK = "_oscilab_toolbar_item"

_Method = TypeVar("_Method")  # A function, or the classmethod/staticmethod wrapping one


@dataclass(frozen=True)
class ToolbarItem:
    kind: Literal["button", "toggle"]
    text: str
    tooltip: str = ""
    checked: bool = False  # A toggle's initial state (not reported to its method)
    toolbar: bool = False  # A producer's: in the toolbar too, not only in its Producers-menu entry


def _mark(item: ToolbarItem) -> Callable[[_Method], _Method]:
    def decorate(method: _Method) -> _Method:
        # Mark the underlying function, so either decorator order works with
        # @classmethod/@staticmethod (which wrap it).
        setattr(getattr(method, "__func__", method), _MARK, item)
        return method

    return decorate


def button(text: str, tooltip: str = "", toolbar: bool = False) -> Callable[[_Method], _Method]:
    """
    Mark a method as a toolbar button: `method()` runs when it's clicked.
    `toolbar`: on a producer, put it in the toolbar as well as its
    Producers-menu entry.
    """
    return _mark(ToolbarItem("button", text, tooltip, toolbar=toolbar))


def toggle(text: str, tooltip: str = "", checked: bool = False, toolbar: bool = False) -> Callable[[_Method], _Method]:
    """
    Mark a method as a toolbar toggle: `method(on)` runs with the new state on
    every click. `checked` is the initial state; the method isn't called for it.
    `toolbar`: on a producer, put it in the toolbar as well as its
    Producers-menu entry.
    """
    return _mark(ToolbarItem("toggle", text, tooltip, checked, toolbar))


def _marked(attr: object) -> ToolbarItem | None:
    return getattr(attr, _MARK, None) or getattr(getattr(attr, "__func__", None), _MARK, None)


def toolbar_items(tools: object, required: bool = True) -> list[tuple[ToolbarItem, Callable[..., object]]]:
    """
    The `@button` / `@toggle` methods of `tools` — an instance, or a class whose
    marked methods are all classmethods — as (item, bound method) pairs, in
    definition order, base classes first. With `required`, having none is an
    error; producers pass False, since most have no controls.
    """
    cls = tools if isinstance(tools, type) else type(tools)
    found: dict[str, tuple[ToolbarItem, object]] = {}
    for klass in reversed(cls.__mro__):
        for name, attr in vars(klass).items():
            item = _marked(attr)
            if item is not None:
                found[name] = (item, attr)  # A subclass's override keeps the base's position
    if not found and required:
        raise ValueError(f"{cls.__name__} has no @button or @toggle methods")

    items = []
    for name, (item, attr) in found.items():
        if tools is cls and not isinstance(attr, classmethod | staticmethod):
            raise TypeError(f"{cls.__name__}.{name} is a regular method: pass an instance, {cls.__name__}(...)")
        items.append((item, getattr(tools, name)))
    return items

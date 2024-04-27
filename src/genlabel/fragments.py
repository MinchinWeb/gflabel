from __future__ import annotations

import functools
import logging
import re
from abc import ABCMeta, abstractmethod
from collections.abc import Callable
from math import cos, pi, radians, sin, tan
from typing import Any, Type

from build123d import (
    Axis,
    BuildLine,
    BuildSketch,
    CenterArc,
    Circle,
    Line,
    Location,
    Mode,
    Plane,
    Polyline,
    Rectangle,
    RegularPolygon,
    Sketch,
    Text,
    add,
    make_face,
    mirror,
)

from .options import RenderOptions

logger = logging.getLogger(__name__)
RE_FRAGMENT = re.compile(r"(.+?)(?:\((.*)\))?$")

FRAGMENTS: dict[str, Type[Fragment] | Callable[..., Fragment]] = {}


def fragment_from_spec(spec: str) -> Fragment:
    # If the fragment is just a number, this is distance to space out
    try:
        value = float(spec)
    except ValueError:
        pass
    else:
        return SpacerFragment(value)

    # Is a fragment name, optionally with arguments
    match = RE_FRAGMENT.match(spec)
    assert match
    name, args = match.groups()
    args = args.split(",") if args else []

    if name not in FRAGMENTS:
        raise RuntimeError(f"Unknown fragment class: {name}")
    return FRAGMENTS[name](*args)


def fragment(*names: str):
    """Register a label fragment generator"""

    def _wrapped(
        fn: Type[Fragment] | Callable[[float, float], Sketch],
    ) -> Type[Fragment] | Callable[[float, float], Sketch]:
        for name in names:
            logger.debug(f"Registering fragment {name}")
            if name == "bolt":
                print("Bolt")
            if not isinstance(fn, type) and callable(fn):
                logger.debug(f"Wrapping fragment function {fn}")

                # We can have callable functions
                # class FnWrapper(Fragment):
                #     def render(
                #         self, height: float, maxsize: float, options: Any
                #     ) -> Sketch:
                #         return orig_fn(height, maxsize)
                FRAGMENTS[name] = lambda *args: FunctionalFragment(fn, *args)
            else:
                FRAGMENTS[name] = fn
        return fn

    return _wrapped


class Fragment(metaclass=ABCMeta):
    # Is this a fixed or variable-width fragment?
    variable_width = False

    # If variable width, higher priority fragments will be rendered first
    priority: float = 1

    # If this fragment is visible. If not visible, rendered sketch will
    # indicate bounding box only, but should never be added.
    visible = True

    def __init__(self, *args: list[Any]):
        if args:
            raise ValueError("Not all fragment arguments handled")

    # If this fragment is variable, what's the smallest it can go?
    def min_width(self, height: float) -> float:
        """If this fragment is variable, what's the smallest it can go?"""
        if self.variable_width:
            raise NotImplementedError(
                f"min_width not implemented for variable width object '{type(self).__name__}'"
            )
        return 0

    @abstractmethod
    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        pass


class FunctionalFragment(Fragment):
    """Simple fragment for registering uncomplicated fragments"""

    def __init__(self, fn: Callable[[float, float], Sketch], *args):
        assert not args
        self.fn = fn

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        return self.fn(height, maxsize)


class SpacerFragment(Fragment):
    visible = False

    def __init__(self, distance: float, *args):
        super().__init__(*args)
        self.distance = distance

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        with BuildSketch() as sketch:
            Rectangle(self.distance, height)
        return sketch.sketch


@fragment("...")
class ExpandingFragment(Fragment):
    """Always expands to fill available space"""

    variable_width = True
    priority = 0
    visible = False

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        with BuildSketch() as sketch:
            Rectangle(maxsize, height)
        return sketch.sketch

    def min_width(self, height: float) -> float:
        return 0


class TextFragment(Fragment):
    def __init__(self, text: str):
        self.text = text

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        with BuildSketch() as sketch:
            print(f"Rendering text {self.text} as {options.font}")
            Text(
                self.text,
                font_size=options.font.font_height_mm or height,
                font=options.font.font,
                font_style=options.font.font_style,
            )
        return sketch.sketch


@functools.lru_cache
def _whitespace_width(spacechar: str, height: float, options: RenderOptions) -> float:
    """Calculate the width of a space at a specific text height"""
    w2 = (
        Text(
            f"a{spacechar}a",
            font_size=options.font.font_height_mm or height,
            font=options.font.font,
            font_style=options.font.font_style,
            mode=Mode.PRIVATE,
        )
        .bounding_box()
        .size.X
    )
    wn = (
        Text(
            "aa",
            font_size=options.font.font_height_mm or height,
            font=options.font.font,
            font_style=options.font.font_style,
            mode=Mode.PRIVATE,
        )
        .bounding_box()
        .size.X
    )
    return w2 - wn


class WhitespaceFragment(Fragment):
    visible = False

    def __init__(self, whitespace: str):
        if not whitespace.isspace():
            raise ValueError(
                f"Whitespace fragment can only contain whitespace, got {whitespace!r}"
            )
        self.whitespace = whitespace

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        with BuildSketch() as sketch:
            Rectangle(_whitespace_width(self.whitespace, height, options), height)
        return sketch.sketch


@fragment("hexhead")
def _fragment_hexhead(height: float, _maxsize: float) -> Sketch:
    with BuildSketch(mode=Mode.PRIVATE) as sketch:
        Circle(height / 2)
        RegularPolygon(height / 2 * 0.6, side_count=6, mode=Mode.SUBTRACT)
    return sketch.sketch


@fragment("hexnut", "nut")
def _fragment_hexnut(height: float, _maxsize: float) -> Sketch:
    with BuildSketch(mode=Mode.PRIVATE) as sketch:
        RegularPolygon(height / 2, side_count=6)
        Circle(height / 2 * 0.4, mode=Mode.SUBTRACT)
    return sketch.sketch


@fragment("washer")
def _fragment_washer(height: float, _maxsize: float) -> Sketch:
    with BuildSketch(mode=Mode.PRIVATE) as sketch:
        Circle(height / 2)
        Circle(height / 2 * 0.4, mode=Mode.SUBTRACT)
    return sketch.sketch


@fragment("bolt")
class BoltFragment(Fragment):
    variable_width = True

    def __init__(self, length: float, *args: list[Any]):
        super().__init__(*args)
        self.length = float(length)

    def min_width(self, height: float) -> float:
        return height

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        length = self.length
        # line width: How thick the head and body are
        lw = height / 2.25
        # The half-width of the dividing split
        half_split = 0.75

        # Don't allow lengths smaller than lw
        # length = max(length, lw * 2 + half_split)
        maxsize = max(maxsize, lw * 2 + half_split * 2 + 0.1)
        # Work out what the total length is, and if this is over max size
        # then we need to cut the bolt
        split_bolt = length + lw > maxsize
        # Halfwidth distance
        if split_bolt:
            hw = maxsize / 2
        else:
            hw = (length + lw) / 2

        if not split_bolt:
            with BuildSketch(mode=Mode.PRIVATE) as sketch:
                with BuildLine() as line:
                    Polyline(
                        [
                            (-hw, 0),
                            (-hw, height / 2),
                            (-hw + lw, height / 2),
                            (-hw + lw, lw / 2),
                            (hw, lw / 2),
                            (hw, 0),
                        ],
                    )
                    mirror(line.line, Plane.XZ)
                make_face()
        else:
            # We need to split the bolt
            with BuildSketch(mode=Mode.PRIVATE) as sketch:
                x_shaft_midpoint = lw + (maxsize - lw) / 2 - hw
                with BuildLine() as line:
                    Polyline(
                        [
                            (-hw, height / 2),
                            (-hw + lw, height / 2),
                            (-hw + lw, lw / 2),
                            # Divider is halfway along the shaft
                            (x_shaft_midpoint + lw / 2 - half_split, lw / 2),
                            (x_shaft_midpoint - lw / 2 - half_split, -lw / 2),
                            (-hw + lw, -lw / 2),
                            (-hw + lw, -height / 2),
                            (-hw, -height / 2),
                        ],
                        close=True,
                    )
                make_face()
                with BuildLine() as line:
                    Polyline(
                        [
                            # Divider is halfway along the shaft
                            (x_shaft_midpoint + lw / 2 + half_split, lw / 2),
                            (hw, lw / 2),
                            (hw, -lw / 2),
                            (x_shaft_midpoint - lw / 2 + half_split, -lw / 2),
                        ],
                        close=True,
                    )
                make_face()
        return sketch.sketch


@fragment("webbolt")
class WebbBoltFragment(Fragment):
    def __init__(self, *heads: str):
        self.heads = heads

    def render(self, height: float, maxsize: float, options: RenderOptions) -> Sketch:
        # 12 mm high for 15 mm wide. Scale to this.
        width = 15 / 12 * height
        # Relative proportion of body:head
        body_w = width / 2
        # How many threads to have on the body
        n_threads = 6
        # How deep each thread profile should be
        thread_depth = 0.5

        # Calculated values
        head_w = width - body_w
        x_head = body_w - width / 2

        x0 = -width / 2
        # Make a zig-zag for the bolt head

        thread_pitch = body_w / n_threads
        thread_lines = []
        thread_tip_height = height / 4 + thread_depth
        for i in range(n_threads):
            thread_lines.extend(
                [
                    (x0 + i * thread_pitch, thread_tip_height - thread_depth),
                    (x0 + (i + 0.5) * thread_pitch, thread_tip_height),
                    # (x0 + (i + 1) * thread_pitch, height / 4),
                ]
            )

        with BuildSketch() as sketch:
            with BuildLine() as line:
                head_radius = 2
                head_arc = CenterArc(
                    (width / 2 - head_radius, height / 2 - head_radius),
                    head_radius,
                    0,
                    90,
                )
                Polyline(
                    [
                        (x0, 0),
                        *thread_lines,
                        (x_head, thread_tip_height - thread_depth),
                        (x_head, height / 2),
                        head_arc @ 1,
                    ]
                )
                Line([head_arc @ 0, (width / 2, 0)])

                mirror(line.line, Plane.XZ)
            make_face()

            # Now, do the heads
            for head in set(self.heads):
                shape = self._headshape(head)
                location = Location((width / 2 - head_w / 2, 0))
                add(shape.scale(head_w * 0.9).locate(location), mode=Mode.SUBTRACT)

        return sketch.sketch

    def _headshape(self, shape: str) -> Sketch:
        shape = shape.lower()
        with BuildSketch(mode=Mode.PRIVATE) as sk:
            if shape in {"phillips", "+"}:
                Rectangle(1, 0.22)
                Rectangle(0.2, 1)
                Rectangle(0.4, 0.4, rotation=45)
            elif shape in {"pozidrive", "posidrive", "posi", "pozi"}:
                # Phillips head
                Rectangle(1, 0.2)
                Rectangle(0.2, 1)
                Rectangle(0.4, 0.4, rotation=45)
                Rectangle(1, 0.1, rotation=45)
                Rectangle(1, 0.1, rotation=-45)
            elif shape in {"slot", "-"}:
                Rectangle(1, 0.2)
            elif shape == "hex":
                RegularPolygon(0.5, side_count=6)

        return sk.sketch


@fragment("variable_resistor")
def _fragment_variable_resistor(height: float, maxsize: float) -> Sketch:
    # symb = import_svg("symbols/variable_resistor.svg")
    t = 0.4 / 2
    w = 6.5
    h = 2
    with BuildSketch(mode=Mode.PRIVATE) as sketch:
        # add(symb)
        # Circle(1)
        # sweep(symb)
        with BuildLine():
            Polyline(
                [
                    (-6, 0),
                    (-6, t),
                    (-w / 2 - t, t),
                    (-w / 2 - t, h / 2 + t),
                    (0, h / 2 + t),
                    (0, h / 2 - t),
                    (-w / 2 + t, h / 2 - t),
                    (-w / 2 + t, 0),
                ],
                close=True,
            )
        make_face()
        mirror(sketch.sketch, Plane.XZ)
        mirror(sketch.sketch, Plane.YZ)

        # with BuildLine():
        l_arr = 7
        l_head = 1.5
        angle = 30

        theta = radians(angle)
        oh = t / tan(theta) + t / sin(theta)
        sigma = pi / 2 - theta
        l_short = t / cos(sigma)
        #     l_arrow = 1.5
        #     angle = 30
        #     # Work out the intersect height
        #     h_i = t / math.sin(math.radians(angle))
        #     arr_bottom_lost = t / math.tan(math.radians(angle))
        arrow_parts = [
            (0, -l_arr / 2),
            (-t, -l_arr / 2),
            (-t, l_arr / 2 - oh),
            (
                -t - sin(theta) * (l_head - l_short),
                l_arr / 2 - oh - cos(theta) * (l_head - l_short),
            ),
            (-sin(theta) * l_head, l_arr / 2 - cos(theta) * l_head),
            (0, l_arr / 2),
            # (0, -l_arr / 2),
        ]
        with BuildSketch(mode=Mode.PRIVATE) as arrow:
            with BuildLine() as line:
                Polyline(
                    arrow_parts,
                    # close=True,
                )
                mirror(line.line, Plane.YZ)
            make_face()
        add(arrow.sketch.rotate(Axis.Z, -30))
        # sketch.sketch.rotate(Axis.Z, 20)
        # with BuildLine() as line:
        #     Line([(0, -arr_h / 2), (0, arr_h / 2)])
        # Arrow(arr_h, line, t * 2, head_at_start=False)
        # mirror(line.line, Plane.XZ)
        # mirror(line.line, Plane.YZ)
        # offset(symb, amount=1)
        # add(symb)
    # Scale to fit in our height, unless this would take us over width
    size = sketch.sketch.bounding_box().size
    scale = height / size.Y
    # actual_w = min(scale * size.X, maxsize)
    # scale = actual_w / size.X

    return sketch.sketch.scale(scale)

# coding: utf8
"""
    cairocffi.patterns
    ~~~~~~~~~~~~~~~~~~

    Bindings for the various types of pattern objects.

    :copyright: Copyright 2013 by Simon Sapin
    :license: BSD, see LICENSE for details.

"""

from . import ffi, cairo, _check_status, Matrix
from .surfaces import Surface
from .compat import xrange


class Pattern(object):
    """The base class for all pattern types.

    Should not be instantiated directly, but see :doc:`cffi_api`.
    An instance may be returned for cairo pattern types
    that are not (yet) defined in cairocffi.

    A :class:`Pattern` represents a source when drawing onto a surface.
    There are different sub-classes of :class:`Pattern`,
    for different types of sources;
    for example, :class:`SolidPattern` is a pattern for a solid color.

    Other than instantiating the various :class:`Pattern` sub-classes,
    some of the pattern types can be implicitly created
    using various :class:`Context`; for example :meth:`Context.set_source_rgb`.

    """
    def __init__(self, pointer):
        self._pointer = ffi.gc(pointer, cairo.cairo_pattern_destroy)
        self._check_status()

    def _check_status(self):
        _check_status(cairo.cairo_pattern_status(self._pointer))

    @staticmethod
    def _from_pointer(pointer, incref):
        """Wrap an existing :c:type:`cairo_pattern_t *` cdata pointer.

        :type incref: bool
        :param incref:
            Whether increase the :ref:`reference count <refcounting>` now.
        :return:
            A new instance of :class:`Pattern` or one of its sub-classes,
            depending on the pattern’s type.

        """
        if pointer == ffi.NULL:
            raise ValueError('Null pointer')
        if incref:
            cairo.cairo_pattern_reference(pointer)
        self = object.__new__(PATTERN_TYPE_TO_CLASS.get(
            cairo.cairo_pattern_get_type(pointer), Pattern))
        Pattern.__init__(self, pointer)  # Skip the subclass’s __init__
        return self

    def set_extend(self, extend):
        """
        Sets the mode to be used for drawing outside the area of this pattern.
        See :ref:`EXTEND` for details on the semantics of each extend strategy.

        The default extend mode is
        :obj:`NONE <EXTEND_NONE>` for :class:`SurfacePattern`
        and :obj:`PAD <EXTEND_PAD>` for :class:`Gradient` patterns.

        """
        cairo.cairo_pattern_set_extend(self._pointer, extend)
        self._check_status()

    def get_extend(self):
        """Gets the current extend mode for this pattern.

        :returns: A :ref:`EXTEND` string.

        """
        return cairo.cairo_pattern_get_extend(self._pointer)

    # pycairo only has filters on SurfacePattern,
    # but cairo seems to accept it on any pattern.
    def set_filter(self, filter):
        """Sets the filter to be used for resizing when using this pattern.
        See :ref:`FILTER` for details on each filter.

        Note that you might want to control filtering
        even when you do not have an explicit :class:`Pattern`,
        (for example when using :meth:`Context.set_source_surface`).
        In these cases, it is convenient to use :meth:`Context.get_source`
        to get access to the pattern that cairo creates implicitly.

        For example::

            context.get_source().set_filter('NEAREST')

        """
        cairo.cairo_pattern_set_filter(self._pointer, filter)
        self._check_status()

    def get_filter(self):
        """Return the current filter string for this pattern.
        See :ref:`FILTER` for details on each filter.

        """
        return cairo.cairo_pattern_get_filter(self._pointer)

    def set_matrix(self, matrix):
        """Sets the pattern’s transformation matrix to :obj:`matrix`.
        This matrix is a transformation from user space to pattern space.

        When a pattern is first created
        it always has the identity matrix for its transformation matrix,
        which means that pattern space is initially identical to user space.

        **Important:**
        Please note that the direction of this transformation matrix
        is from user space to pattern space.
        This means that if you imagine the flow
        from a pattern to user space (and on to device space),
        then coordinates in that flow will be transformed
        by the inverse of the pattern matrix.

        For example, if you want to make a pattern appear twice as large
        as it does by default the correct code to use is::

            pattern.set_matrix(Matrix(xx=0.5, yy=0.5))

        Meanwhile, using values of 2 rather than 0.5 in the code above
        would cause the pattern to appear at half of its default size.

        Also, please note the discussion of the user-space locking semantics
        of :meth:`Context.set_source`.

        :param matrix: A :class:`Matrix` to be copied into the pattern.

        """
        cairo.cairo_pattern_set_matrix(self._pointer, matrix._pointer)
        self._check_status()

    def get_matrix(self):
        """Copies the pattern’s transformation matrix.

        :retuns: A new :class:`Matrix` object.

        """
        matrix = Matrix()
        cairo.cairo_pattern_get_matrix(self._pointer, matrix._pointer)
        self._check_status()
        return matrix


class SolidPattern(Pattern):
    """Creates a new pattern corresponding to a solid color.
    The color and alpha components are in the range 0 to 1.
    If the values passed in are outside that range, they will be clamped.

    :param red: Red component of the color.
    :param green: Green component of the color.
    :param blue: Blue component of the color.
    :param alpha:
        Alpha component of the color.
        1 (the default) is opaque, 0 fully transparent.
    :type red: float
    :type green: float
    :type blue: float
    :type alpha: float

    """
    def __init__(self, red, green, blue, alpha=1):
        Pattern.__init__(
            self, cairo.cairo_pattern_create_rgba(red, green, blue, alpha))

    def get_rgba(self):
        """Returns the solid pattern’s color.

        :returns: a ``(red, green, blue, alpha)`` tuple of floats.

        """
        rgba = ffi.new('double[4]')
        _check_status(cairo.cairo_pattern_get_rgba(
            self._pointer, rgba + 0, rgba + 1, rgba + 2, rgba + 3))
        return tuple(rgba)


class SurfacePattern(Pattern):
    """Create a new pattern for the given surface.

    :param surface: A :class:`Surface` object.

    """
    def __init__(self, surface):
        Pattern.__init__(
            self, cairo.cairo_pattern_create_for_surface(surface._pointer))

    def get_surface(self):
        """Return this :class:`SurfacePattern`’s surface.

        :returns:
            An instance of :class:`Surface` or one of its sub-classes,
            a new Python object referencing the existing cairo surface.

        """
        surface_p = ffi.new('cairo_surface_t **')
        _check_status(cairo.cairo_pattern_get_surface(
            self._pointer, surface_p))
        return Surface._from_pointer(surface_p[0], incref=True)


class Gradient(Pattern):
    """
    The common parent of :class:`LinearGradient` and :class:`RadialGradient`.
    Should not be instantiated directly.

    """
    def add_color_stop_rgba(self, offset, red, green, blue, alpha=1):
        """Adds a translucent color stop to a gradient pattern.

        The offset specifies the location along the gradient's control vector.
        For example,
        a linear gradient's control vector is from (x0,y0) to (x1,y1)
        while a radial gradient's control vector is
        from any point on the start circle
        to the corresponding point on the end circle.

        If two (or more) stops are specified with identical offset values,
        they will be sorted
        according to the order in which the stops are added
        (stops added earlier before stops added later).
        This can be useful for reliably making sharp color transitions
        instead of the typical blend.

        The color components and offset are in the range 0 to 1.
        If the values passed in are outside that range, they will be clamped.

        :param offset: Location along the gradient's control vector
        :param red: Red component of the color.
        :param green: Green component of the color.
        :param blue: Blue component of the color.
        :param alpha:
            Alpha component of the color.
            1 (the default) is opaque, 0 fully transparent.
        :type offset: float
        :type red: float
        :type green: float
        :type blue: float
        :type alpha: float

        """
        cairo.cairo_pattern_add_color_stop_rgba(
            self._pointer, offset, red, green, blue, alpha)
        self._check_status()

    def add_color_stop_rgb(self, offset, red, green, blue):
        """Same as :meth:`add_color_stop_rgba` with ``alpha=1``.
        Kept for compatibility with pycairo.

        """
        cairo.cairo_pattern_add_color_stop_rgb(
            self._pointer, offset, red, green, blue)
        self._check_status()

    def get_color_stops(self):
        """Return this gradient’s color stops so far.

        :returns:
            A list of ``(offset, red, green, blue, alpha)`` tuples of floats.

        """
        count = ffi.new('int *')
        _check_status(cairo.cairo_pattern_get_color_stop_count(
            self._pointer, count))
        stops = []
        stop = ffi.new('double[5]')
        for i in xrange(count[0]):
            _check_status(cairo.cairo_pattern_get_color_stop_rgba(
                self._pointer, i,
                stop + 0, stop + 1, stop + 2, stop + 3, stop + 4))
            stops.append(tuple(stop))
        return stops


class LinearGradient(Gradient):
    """Create a new linear gradient
    along the line defined by (x0, y0) and (x1, y1).
    Before using the gradient pattern, a number of color stops
    should be defined using :meth:`~Gradient.add_color_stop_rgba`.

    Note: The coordinates here are in pattern space.
    For a new pattern, pattern space is identical to user space,
    but the relationship between the spaces can be changed
    with :meth:`~Pattern.set_matrix`.

    :param x0: X coordinate of the start point.
    :param y0: Y coordinate of the start point.
    :param x1: X coordinate of the end point.
    :param y1: Y coordinate of the end point.
    :type x0: float
    :type y0: float
    :type x1: float
    :type y1: float

    """
    def __init__(self, x0, y0, x1, y1):
        Pattern.__init__(
            self, cairo.cairo_pattern_create_linear(x0, y0, x1, y1))

    def get_linear_points(self):
        """Return this linear gradient’s endpoints.

        :returns: A ``(x0, y0, x1, y1)`` tuple of floats.

        """
        points = ffi.new('double[4]')
        _check_status(cairo.cairo_pattern_get_linear_points(
            self._pointer, points + 0, points + 1, points + 2, points + 3))
        return tuple(points)


class RadialGradient(Gradient):
    """Creates a new radial gradient pattern between the two circles
    defined by (cx0, cy0, radius0) and (cx1, cy1, radius1).
    Before using the gradient pattern, a number of color stops
    should be defined using :meth:`~Gradient.add_color_stop_rgba`.

    Note: The coordinates here are in pattern space.
    For a new pattern, pattern space is identical to user space,
    but the relationship between the spaces can be changed
    with :meth:`~Pattern.set_matrix`.

    :param cx0: X coordinate of the start circle.
    :param cy0: Y coordinate of the start circle.
    :param radius0: Radius of the start circle.
    :param cx1: X coordinate of the end circle.
    :param cy1: Y coordinate of the end circle.
    :param radius1: Y coordinate of the end circle.
    :type cx0: float
    :type cy0: float
    :type radius0: float
    :type cx1: float
    :type cy1: float
    :type radius1: float

    """
    def __init__(self, cx0, cy0, radius0, cx1, cy1, radius1):
        Pattern.__init__(self, cairo.cairo_pattern_create_radial(
            cx0, cy0, radius0, cx1, cy1, radius1))

    def get_radial_circles(self):
        """Return this radial gradient’s endpoint circles,
        each specified as a center coordinate and a radius.

        :returns: A ``(cx0, cy0, radius0, cx1, cy1, radius1)`` tuple of floats.

        """
        circles = ffi.new('double[6]')
        _check_status(cairo.cairo_pattern_get_radial_circles(
            self._pointer,  circles + 0, circles + 1, circles + 2,
            circles + 3, circles + 4, circles + 5))
        return tuple(circles)


PATTERN_TYPE_TO_CLASS = {
    'SOLID': SolidPattern,
    'SURFACE': SurfacePattern,
    'LINEAR': LinearGradient,
    'RADIAL': RadialGradient,
}

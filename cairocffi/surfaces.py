# coding: utf8
"""
    cairocffi.surface
    ~~~~~~~~~~~~~~~~~

    Bindings for the various types of surface objects.

    :copyright: Copyright 2013 by Simon Sapin
    :license: BSD, see LICENSE for details.

"""

import io
import sys
import ctypes

from . import ffi, cairo, _check_status
from .fonts import FontOptions, _encode_string


SURFACE_TARGET_KEY = ffi.new('cairo_user_data_key_t *')


def _make_read_func(file_obj):
    """Return a CFFI callback that reads from a file-like object."""
    @ffi.callback("cairo_read_func_t", error='READ_ERROR')
    def read_func(closure, data, length):
        string = file_obj.read(length)
        if len(string) < length:  # EOF too early
            return 'READ_ERROR'
        ffi.buffer(data, length)[:len(string)] = string
        return 'SUCCESS'
    return read_func


def _make_write_func(file_obj):
    """Return a CFFI callback that writes to a file-like object."""
    if file_obj is None:
        return ffi.NULL

    @ffi.callback("cairo_write_func_t", error='WRITE_ERROR')
    def read_func(_closure, data, length):
        file_obj.write(ffi.buffer(data, length))
        return 'SUCCESS'
    return read_func


def _encode_filename(filename):
    """Return a byte string, encoding Unicode with the filesystem encoding."""
    if not isinstance(filename, bytes):
        filename = filename.encode(sys.getfilesystemencoding())
    return ffi.new('char[]', filename)


def from_buffer(obj):
    """Return ``(pointer_address, length_in_bytes)`` for a buffer object."""
    if hasattr(obj, 'buffer_info'):
        # Looks like a array.array object.
        address, length = obj.buffer_info()
        return address, length * obj.itemsize
    else:
        # Other buffers.
        # XXX Unfortunately ctypes.c_char.from_buffer
        # does not have length information,
        # and we’re not sure that len(obj) is measured in bytes.
        # (It’s not for array.array, though that is taken care of.)
        return ctypes.addressof(ctypes.c_char.from_buffer(obj)), len(obj)


class KeepAlive(object):
    """
    Keep some objects alive until a callback is called.
    :attr:`closure` is a tuple of cairo_destroy_func_t and void* cdata objects,
    as expected by cairo_surface_set_mime_data().

    Either :meth:`save` must be called before the callback,
    or none of them must be called.

    """
    instances = set()

    def __init__(self, *objects):
        self.objects = objects
        callback = ffi.callback(
            'cairo_destroy_func_t', lambda _: self.instances.remove(self))
        # cairo wants a non-NULL closure pointer.
        self.closure = (callback, callback)

    def save(self):
        """Start keeping a reference to the passed objects."""
        self.instances.add(self)


class Surface(object):
    """The base class for all surface types.

    Should not be instantiated directly, but see :doc:`cffi_api`.
    An instance may be returned for cairo surface types
    that are not (yet) defined in cairocffi.

    A :class:`Surface` represents an image,
    either as the destination of a drawing operation
    or as source when drawing onto another surface.
    To draw to a :class:`Surface`,
    create a cairo :class:`Context` with the surface as the target.

    There are different sub-classes of :class:`Surface`
    for different drawing backends;
    for example, :class:`ImageSurface` is a bitmap image in memory.

    The initial contents of a surface after creation
    depend upon the manner of its creation.
    If cairo creates the surface and backing storage for the user,
    it will be initially cleared;
    for example, :class:`ImageSurface` and :meth:`create_similar`.
    Alternatively, if the user passes in a reference
    to some backing storage and asks cairo to wrap that in a :class:`Surface`,
    then the contents are not modified;
    for example, :class:`ImageSurface` with a :obj:`data` argument.

    """
    def __init__(self, pointer, target_keep_alive=None):
        self._pointer = ffi.gc(pointer, cairo.cairo_surface_destroy)
        self._check_status()
        if target_keep_alive not in (None, ffi.NULL):
            keep_alive = KeepAlive(target_keep_alive)
            _check_status(cairo.cairo_surface_set_user_data(
                self._pointer, SURFACE_TARGET_KEY, *keep_alive.closure))
            keep_alive.save()

    def _check_status(self):
        _check_status(cairo.cairo_surface_status(self._pointer))

    @staticmethod
    def _from_pointer(pointer, incref):
        """Wrap an existing :c:type:`cairo_surface_t *` cdata pointer.

        :type incref: bool
        :param incref:
            Whether increase the :ref:`reference count <refcounting>` now.
        :return:
            A new instance of :class:`Surface` or one of its sub-classes,
            depending on the surface’s type.

        """
        if pointer == ffi.NULL:
            raise ValueError('Null pointer')
        if incref:
            cairo.cairo_surface_reference(pointer)
        self = object.__new__(SURFACE_TYPE_TO_CLASS.get(
            cairo.cairo_surface_get_type(pointer), Surface))
        Surface.__init__(self, pointer)  # Skip the subclass’s __init__
        return self

    def create_similar(self, content, width, height):
        """Create a new surface that is as compatible as possible
        for uploading to and the use in conjunction with this surface.
        For example the new surface will have the same fallback resolution
        and :class:`FontOptions`.
        Generally, the new surface will also use the same backend as other,
        unless that is not possible for some reason.

        Initially the surface contents are all 0
        (transparent if contents have transparency, black otherwise.)

        Use :meth:`create_similar_image` if you need an image surface
        which can be painted quickly to the target surface.

        :param content: the :ref:`CONTENT` string for the new surface.
        :param width: width of the new surface (in device-space units)
        :param height: height of the new surface (in device-space units)
        :type content: str
        :type width: int
        :type height: int
        :returns: A new instance of :class:`Surface` or one of its subclasses.

        """
        return Surface._from_pointer(
            cairo.cairo_surface_create_similar(
                self._pointer, content, width, height),
            incref=False)

    def create_similar_image(self, content, width, height):
        """
        Create a new image surface that is as compatible as possible
        for uploading to and the use in conjunction with this surface.
        However, this surface can still be used like any normal image surface.

        Initially the surface contents are all 0
        (transparent if contents have transparency, black otherwise.)

        Use :meth:`create_similar` if you don't need an image surface.

        :param format: the :ref:`FORMAT` string for the new surface
        :param width: width of the new surface, (in device-space units)
        :param height: height of the new surface (in device-space units)
        :type format: str
        :type width: int
        :type height: int
        :returns: A new :class:`ImageSurface` instance.

        """
        return Surface._from_pointer(
            cairo.cairo_surface_create_similar_image(
                self._pointer, content, width, height),
            incref=False)

    def create_for_rectangle(self, x, y, width, height):
        """
        Create a new surface that is a rectangle within this surface.
        All operations drawn to this surface are then clipped and translated
        onto the target surface.
        Nothing drawn via this sub-surface outside of its bounds
        is drawn onto the target surface,
        making this a useful method for passing constrained child surfaces
        to library routines that draw directly onto the parent surface,
        i.e. with no further backend allocations,
        double buffering or copies.

        .. note::

            As of cairo 1.12,
            the semantics of subsurfaces have not been finalized yet
            unless the rectangle is in full device units,
            is contained within the extents of the target surface,
            and the target or subsurface's device transforms are not changed.

        :param x:
            The x-origin of the sub-surface
            from the top-left of the target surface (in device-space units)
        :param y:
            The y-origin of the sub-surface
            from the top-left of the target surface (in device-space units)
        :param width:
            Width of the sub-surface (in device-space units)
        :param height:
            Height of the sub-surface (in device-space units)
        :type x: float
        :type y: float
        :type width: float
        :type height: float
        :returns:
            A new :class:`Surface` object.

        *New in cairo 1.10.*

        """
        return Surface._from_pointer(
            cairo.cairo_surface_create_for_rectangle(
                self._pointer, x, y, width, height),
            incref=False)

    def get_content(self):
        """Returns the :ref:`CONTENT` string of this surface,
        which indicates whether the surface contains color
        and/or alpha information.

        """
        return cairo.cairo_surface_get_content(self._pointer)

    def has_show_text_glyphs(self):
        """Returns whether the surface supports sophisticated
        :meth:`Context.show_text_glyphs` operations.
        That is, whether it actually uses the text and cluster data
        provided to a :meth:`Context.show_text_glyphs` call.

        .. note::

            Even if this method returns :obj:`False`,
            :meth:`Context.show_text_glyphs` operation targeted at surface
            will still succeed.
            It just will act like a :meth:`Context.show_glyphs` operation.
            Users can use this method to avoid computing UTF-8 text
            and cluster mapping if the target surface does not use it.

        """
        return bool(cairo.cairo_surface_has_show_text_glyphs(self._pointer))

    def set_device_offset(self, x_offset, y_offset):
        """ Sets an offset that is added to the device coordinates
        determined by the CTM when drawing to surface.
        One use case for this method is
        when we want to create a :class:`Surface` that redirects drawing
        for a portion of an onscreen surface
        to an offscreen surface in a way that is
        completely invisible to the user of the cairo API.
        Setting a transformation via :meth:`Context.translate`
        isn't sufficient to do this,
        since methods like :meth:`Context.device_to_user`
        will expose the hidden offset.

        Note that the offset affects drawing to the surface
        as well as using the surface in a source pattern.

        :param x_offset:
            The offset in the X direction, in device units
        :param y_offset:
            The offset in the Y direction, in device units

        """
        cairo.cairo_surface_set_device_offset(
            self._pointer, x_offset, y_offset)
        self._check_status()

    def get_device_offset(self):
        """Returns the previous device offset set by :meth:`set_device_offset`.

        :returns: ``(x_offset, y_offset)``

        """
        offsets = ffi.new('double[2]')
        cairo.cairo_surface_get_device_offset(
            self._pointer, offsets + 0, offsets + 1)
        return tuple(offsets)

    def set_fallback_resolution(self, x_pixels_per_inch, y_pixels_per_inch):
        """
        Set the horizontal and vertical resolution for image fallbacks.

        When certain operations aren't supported natively by a backend,
        cairo will fallback by rendering operations to an image
        and then overlaying that image onto the output.
        For backends that are natively vector-oriented,
        this method can be used to set the resolution
        used for these image fallbacks,
        (larger values will result in more detailed images,
        but also larger file sizes).

        Some examples of natively vector-oriented backends are
        the ps, pdf, and svg backends.

        For backends that are natively raster-oriented,
        image fallbacks are still possible,
        but they are always performed at the native device resolution.
        So this method has no effect on those backends.

        .. note::

            The fallback resolution only takes effect
            at the time of completing a page
            (with :meth:`show_page` or :meth:`copy_page`)
            so there is currently no way to have
            more than one fallback resolution in effect on a single page.

        The default fallback resoultion is
        300 pixels per inch in both dimensions.

        :param x_pixels_per_inch: horizontal resolution in pixels per inch
        :type x_pixels_per_inch: float
        :param y_pixels_per_inch: vertical resolution in pixels per inch
        :type y_pixels_per_inch: float

        """
        cairo.cairo_surface_set_fallback_resolution(
            self._pointer, x_pixels_per_inch, y_pixels_per_inch)
        self._check_status()

    def get_fallback_resolution(self):
        """Returns the previous fallback resolution
        set by :meth:`set_fallback_resolution`,
        or default fallback resolution if never set.

        :returns: ``(x_pixels_per_inch, y_pixels_per_inch)``

        """
        ppi = ffi.new('double[2]')
        cairo.cairo_surface_get_fallback_resolution(
            self._pointer, ppi + 0, ppi + 1)
        return tuple(ppi)

    def get_font_options(self):
        """ Retrieves the default font rendering options for the surface.

        This allows display surfaces to report the correct subpixel order
        for rendering on them,
        print surfaces to disable hinting of metrics and so forth.
        The result can then be used with :class:`ScaledFont`.

        :returns: A new :class:`FontOptions` object.

        """
        font_options = FontOptions()
        cairo.cairo_surface_get_font_options(
            self._pointer, font_options._pointer)
        return font_options

    def set_mime_data(self, mime_type, data):
        """
         Attach an image in the format :obj:`mime_type` to this surface.

         To remove the data from a surface,
         call this method with same mime type and :obj:`None` for data.

        The attached image (or filename) data can later
        be used by backends which support it
        (currently: PDF, PS, SVG and Win32 Printing surfaces)
        to emit this data instead of making a snapshot of the surface.
        This approach tends to be faster
        and requires less memory and disk space.

        The recognized MIME types are the following:

        ``"image/png"``
            The Portable Network Graphics image file format (ISO/IEC 15948).
        ``"image/jpeg"``
            The Joint Photographic Experts Group (JPEG)
            image coding standard (ISO/IEC 10918-1).
        ``"image/jp2"``
            The Joint Photographic Experts Group (JPEG) 2000
            image coding standard (ISO/IEC 15444-1).
        ``"text/x-uri"``
            URL for an image file (unofficial MIME type).

        See corresponding backend surface docs
        for details about which MIME types it can handle.
        Caution: the associated MIME data will be discarded
        if you draw on the surface afterwards.
        Use this method with care.

        :param mime_type: The MIME type of the image data.
        :type mime_type: ASCII string
        :param data: The image data to attach to the surface.
        :type data: bytes

        *New in cairo 1.10.*

        """
        mime_type = ffi.new('char[]', mime_type.encode('utf8'))
        if data is None:
            _check_status(cairo.cairo_surface_set_mime_data(
                self._pointer, mime_type, ffi.NULL, 0, ffi.NULL, ffi.NULL))
        else:
            # TODO: avoid making a copy here if possible.
            length = len(data)
            data = ffi.new('char[]', data)
            keep_alive = KeepAlive(data, mime_type)
            _check_status(cairo.cairo_surface_set_mime_data(
                self._pointer, mime_type, data, length,
                *keep_alive.closure))
            keep_alive.save()  # Only on success

    def get_mime_data(self, mime_type):
        """Return mime data previously attached to surface
        using the specified mime type.

        :param mime_type: The MIME type of the image data.
        :type mime_type: ASCII string
        :returns:
            A CFFI buffer object, or :obj:`None`
            if no data has been attached with the given mime type.

        *New in cairo 1.10.*

        """
        buffer_address = ffi.new('unsigned char **')
        buffer_length = ffi.new('unsigned long *')
        mime_type = ffi.new('char[]', mime_type.encode('utf8'))
        cairo.cairo_surface_get_mime_data(
            self._pointer, mime_type, buffer_address, buffer_length)
        return (ffi.buffer(buffer_address[0], buffer_length[0])
                if buffer_address[0] != ffi.NULL else None)

    def supports_mime_type(self, mime_type):
        """ Return whether surface supports :obj:`mime_type`.

        :param mime_type: The MIME type of the image data.
        :type mime_type: ASCII string

        *New in cairo 1.12.*

        """
        mime_type = ffi.new('char[]', mime_type.encode('utf8'))
        return bool(cairo.cairo_surface_supports_mime_type(
            self._pointer, mime_type))

    def mark_dirty(self):
        """Tells cairo that drawing has been done to surface
        using means other than cairo,
        and that cairo should reread any cached areas.
        Note that you must call :meth:`flush` before doing such drawing.

        """
        cairo.cairo_surface_mark_dirty(self._pointer)
        self._check_status()

    def mark_dirty_rectangle(self, x, y, width, height):
        """
        Like :meth:`mark_dirty`,
        but drawing has been done only to the specified rectangle,
        so that cairo can retain cached contents
        for other parts of the surface.

        Any cached clip set on the surface will be reset by this method,
        to make sure that future cairo calls have the clip set
        that they expect.

        :param x: X coordinate of dirty rectangle.
        :param y: Y coordinate of dirty rectangle.
        :param width: Width of dirty rectangle.
        :param height: Height of dirty rectangle.
        :type x: float
        :type y: float
        :type width: float
        :type height: float

        """
        cairo.cairo_surface_mark_dirty_rectangle(
            self._pointer, x, y, width, height)
        self._check_status()

    def show_page(self):
        """Emits and clears the current page
        for backends that support multiple pages.
        Use :meth:`copy_page` if you don't want to clear the page.

        :meth:`Context.show_page` is a convenience method for this.

        """
        cairo.cairo_surface_show_page(self._pointer)
        self._check_status()

    def copy_page(self):
        """Emits the current page for backends that support multiple pages,
        but doesn't clear it,
        so that the contents of the current page will be retained
        for the next page.

        Use :meth:`show_page` if you want to get an empty page
        after the emission.

        """
        cairo.cairo_surface_copy_page(self._pointer)
        self._check_status()

    def flush(self):
        """Do any pending drawing for the surface
        and also restore any temporary modifications
        cairo has made to the surface's state.
        This method must be called before switching
        from drawing on the surface with cairo
        to drawing on it directly with native APIs.
        If the surface doesn't support direct access,
        then this method does nothing.

        """
        cairo.cairo_surface_flush(self._pointer)
        self._check_status()

    def finish(self):
        """This method finishes the surface
        and drops all references to external resources.
        For example, for the Xlib backend it means that
        cairo will no longer access the drawable, which can be freed.
        After calling :meth:`finish` the only valid operations on a surface
        are getting and setting user data, flushing and finishing it.
        Further drawing to the surface will not affect the surface
        but will instead trigger a :class:`CairoError`
        with a ``SURFACE_FINISHED`` status.

        When the surface is garbage-collected, cairo will call :meth:`finish()`
        if it hasn't been called already,
        before freeing the resources associated with the surface.

        """
        cairo.cairo_surface_finish(self._pointer)
        self._check_status()

    def write_to_png(self, target=None):
        """Writes the contents of surface as a PNG image.

        :param target:
            A filename,
            a binary mode file-like object with a :meth:`~file.write` method,
            or :obj:`None`.
        :returns:
            If :obj:`target` is :obj:`None`,
            return the PNG contents as a byte string.

        """
        return_bytes = target is None
        if return_bytes:
            target = io.BytesIO()
        if hasattr(target, 'write'):
            write_func = _make_write_func(target)
            _check_status(cairo.cairo_surface_write_to_png_stream(
                self._pointer, write_func, ffi.NULL))
        else:
            _check_status(cairo.cairo_surface_write_to_png(
                self._pointer, _encode_filename(target)))
        if return_bytes:
            return target.getvalue()


class ImageSurface(Surface):
    """Creates an image surface of the specified format and dimensions.

    If :obj:`data` is not :obj:`None`
    its initial contents will be used as the initial image contents;
    you must explicitly clear the buffer,
    using, for example, :meth:`Context.rectangle` and :meth:`Context.fill`
    if you want it cleared.

    .. note::

        Currently only :class:`array.array` buffers are supported on PyPy.

    Otherwise, the surface contents are all initially 0.
    (Specifically, within each pixel, each color or alpha channel
    belonging to format will be 0.
    The contents of bits within a pixel,
    but not belonging to the given format are undefined).

    :param format: :ref:`FORMAT` string for the surface to create.
    :param width: Width of the surface, in pixels.
    :param height: Height of the surface, in pixels.
    :param data:
        Buffer supplied in which to write contents,
        or :obj:`None` to create a new buffer.
    :param stride:
        The number of bytes between the start of rows
        in the buffer as allocated.
        This value should always be computed by :meth:`format_stride_for_width`
        before allocating the data buffer.
        If omitted but :obj:`data` is given,
        :meth:`format_stride_for_width` is used.
    :type format: str
    :type width: int
    :type height: int
    :type stride: int

    """
    def __init__(self, format, width, height, data=None, stride=None):
        if data is None:
            pointer = cairo.cairo_image_surface_create(format, width, height)
        else:
            if stride is None:
                stride = self.format_stride_for_width(format, width)
            address, length = from_buffer(data)
            if length < stride * height:
                raise ValueError('Got a %d bytes buffer, needs at least %d.'
                                 % (length, stride * height))
            pointer = cairo.cairo_image_surface_create_for_data(
                ffi.cast('char*', address), format, width, height, stride)
        Surface.__init__(self, pointer, target_keep_alive=data)

    @classmethod
    def create_for_data(cls, data, format, width, height, stride=None):
        """Same as ``ImageSurface(format, width, height, data, stride)``.
        Exists for compatibility with pycairo.

        """
        return cls(format, width, height, data, stride)

    @staticmethod
    def format_stride_for_width(format, width):
        """
        This method provides a stride value (byte offset between rows)
        that will respect all alignment requirements
        of the accelerated image-rendering code within cairo.
        Typical usage will be of the form::

            from cairocffi import ImageSurface
            stride = ImageSurface.stride_for_width(format, width)
            data = bytearray(stride * height)
            surface = ImageSurface(format, width, height, data, stride)

        :param format: A :ref:`FORMAT` string.
        :param width: The desired width of the surface, in pixels.
        :type format: str
        :type width: int
        :returns:
            The appropriate stride to use given the desired format and width,
            or -1 if either the format is invalid or the width too large.

        """
        return cairo.cairo_format_stride_for_width(format, width)

    @classmethod
    def create_from_png(cls, source):
        """Decode a PNG file into a new image surface.

        :param source:
            A filename or
            a binary mode file-like object with a :meth:`~file.read` method.
            If you already have a byte string in memory,
            use :class:`io.BytesIO`.
        :returns: A new :class:`ImageSurface` instance.

        """
        if hasattr(source, 'read'):
            read_func = _make_read_func(source)
            pointer = cairo.cairo_image_surface_create_from_png_stream(
                read_func, ffi.NULL)
        else:
            pointer = cairo.cairo_image_surface_create_from_png(
                _encode_filename(source))
        self = object.__new__(cls)
        Surface.__init__(self, pointer)  # Skip ImageSurface.__init__
        return self

    def get_data(self):
        """Return the buffer pointing to the image’s pixel data,
        encoded according to the surface’s :ref:`FORMAT` string.

        A call to :meth:`flush` is required before accessing the pixel data
        to ensure that all pending drawing operations are finished.
        A call to :meth:`mark_dirty` is required after the data is modified.

        :returns: A read-write CFFI buffer object.

        """
        return ffi.buffer(
            cairo.cairo_image_surface_get_data(self._pointer),
            size=self.get_stride() * self.get_height())

    def get_format(self):
        """Return the :ref:`FORMAT` string of the surface."""
        return cairo.cairo_image_surface_get_format(self._pointer)

    def get_width(self):
        """Return the width of the surface, in pixels."""
        return cairo.cairo_image_surface_get_width(self._pointer)

    def get_height(self):
        """Return the width of the surface, in pixels."""
        return cairo.cairo_image_surface_get_height(self._pointer)

    def get_stride(self):
        """Return the stride of the image surface in bytes
        (or 0 if surface is not an image surface).

        The stride is the distance in bytes
        from the beginning of one row of the image data
        to the beginning of the next row.

        """
        return cairo.cairo_image_surface_get_stride(self._pointer)


class PDFSurface(Surface):
    """Creates a PDF surface of the specified size in PostScript points
    to be written to :obj:`target`.

    Note that the size of individual pages of the PDF output can vary.
    See :meth:`set_size`.

    The PDF surface backend recognizes the following MIME types
    for the data attached to a surface (see :meth:`~Surface.set_mime_data`)
    when it is used as a source pattern for drawing on this surface:
    ``image/jpeg`` and
    ``image/jp2``.
    If any of them is specified, the PDF backend emits an image
    with the content of MIME data
    (with the ``/DCTDecode`` or ``/JPXDecode`` filter, respectively)
    instead of a surface snapshot
    (with the ``/FlateDecode`` filter),
    which typically produces PDF with a smaller file size.

    :obj:`target` can be :obj:`None` to specify no output.
    This will generate a surface that may be queried and used as a source,
    without generating a temporary file.

    :param target:
        A filename,
        a binary mode file-like object with a :meth:`~file.write` method,
        or :obj:`None`.
    :param width_in_points:
        Width of the surface, in points (1 point == 1/72.0 inch)
    :param height_in_points:
        Height of the surface, in points (1 point == 1/72.0 inch)
    :type width_in_points: float
    :type height_in_points: float

    """
    def __init__(self, target, width_in_points, height_in_points):
        if hasattr(target, 'write') or target is None:
            write_func = _make_write_func(target)
            pointer = cairo.cairo_pdf_surface_create_for_stream(
                write_func, ffi.NULL, width_in_points, height_in_points)
        else:
            write_func = None
            pointer = cairo.cairo_pdf_surface_create(
                _encode_filename(target), width_in_points, height_in_points)
        Surface.__init__(self, pointer, target_keep_alive=write_func)

    def set_size(self, width_in_points, height_in_points):
        """Changes the size of a PDF surface
        for the current (and subsequent) pages.

        This method should only be called
        before any drawing operations have been performed on the current page.
        The simplest way to do this is to call this method
        immediately after creating the surface
        or immediately after completing a page with either
        :meth:`~Surface.show_page` or :meth:`~Surface.copy_page`.

        :param width_in_points:
            New width of the page, in points (1 point == 1/72.0 inch)
        :param height_in_points:
            New height of the page, in points (1 point == 1/72.0 inch)
        :type width_in_points: float
        :type height_in_points: float

        """
        cairo.cairo_pdf_surface_set_size(
            self._pointer, width_in_points, height_in_points)
        self._check_status()

    def restrict_to_version(self, version):
        """Restricts the generated PDF file to :obj:`version`.

        See :meth:`get_versions` for a list of available version values
        that can be used here.

        This method should only be called
        before any drawing operations have been performed on the given surface.
        The simplest way to do this is to call this method
        immediately after creating the surface.

        :param version: A :ref:`PDF_VERSION` string.

        *New in cairo 1.10.*

        """
        cairo.cairo_pdf_surface_restrict_to_version(self._pointer, version)
        self._check_status()

    @staticmethod
    def get_versions():
        """Return the list of supported PDF versions.
        See :meth:`restrict_to_version`.

        :return: A list of :ref:`PDF_VERSION` strings.

        *New in cairo 1.10.*

        """
        versions = ffi.new('cairo_pdf_version_t const **')
        num_versions = ffi.new('int *')
        cairo.cairo_pdf_get_versions(versions, num_versions)
        versions = versions[0]
        return [versions[i] for i in range(num_versions[0])]

    @staticmethod
    def version_to_string(version):
        """Return the string representation of the given :ref:`PDF_VERSION`.
        See :meth:`get_versions` for a way to get
        the list of valid version ids.

        *New in cairo 1.10.*

        """
        c_string = cairo.cairo_pdf_version_to_string(version)
        if c_string == ffi.NULL:
            raise ValueError(version)
        return ffi.string(c_string).decode('ascii')


class PSSurface(Surface):
    """Creates a PostScript surface of the specified size in PostScript points
    to be written to :obj:`target`.

    Note that the size of individual pages of the PostScript output can vary.
    See :meth:`set_size`.

    :obj:`target` can be :obj:`None` to specify no output.
    This will generate a surface that may be queried and used as a source,
    without generating a temporary file.

    The PostScript surface backend recognizes the ``image/jpeg`` MIME type
    for the data attached to a surface (see :meth:`~Surface.set_mime_data`)
    when it is used as a source pattern for drawing on this surface.
    If it is specified, the PostScript backend emits an image
    with the content of MIME data (with the ``/DCTDecode`` filter)
    instead of a surface snapshot (with the ``/FlateDecode`` filter),
    which typically produces PostScript with a smaller file size.

    :param target:
        A filename,
        a binary mode file-like object with a :meth:`~file.write` method,
        or :obj:`None`.
    :param width_in_points:
        Width of the surface, in points (1 point == 1/72.0 inch)
    :param height_in_points:
        Height of the surface, in points (1 point == 1/72.0 inch)
    :type width_in_points: float
    :type height_in_points: float

    """
    def __init__(self, target, width_in_points, height_in_points):
        if hasattr(target, 'write') or target is None:
            write_func = _make_write_func(target)
            pointer = cairo.cairo_ps_surface_create_for_stream(
                write_func, ffi.NULL, width_in_points, height_in_points)
        else:
            write_func = None
            pointer = cairo.cairo_ps_surface_create(
                _encode_filename(target), width_in_points, height_in_points)
        Surface.__init__(self, pointer, target_keep_alive=write_func)

    def dsc_comment(self, comment):
        """ Emit a comment into the PostScript output for the given surface.

        The comment is expected to conform to
        the PostScript Language Document Structuring Conventions (DSC).
        Please see that manual for details on the available comments
        and their meanings.
        In particular, the ``%%IncludeFeature`` comment allows
        a device-independent means of controlling printer device features.
        So the PostScript Printer Description Files Specification
        will also be a useful reference.

        The comment string must begin with a percent character (%)
        and the total length of the string
        (including any initial percent characters)
        must not exceed 255 bytes.
        Violating either of these conditions will
        place surface into an error state.
        But beyond these two conditions,
        this method will not enforce conformance of the comment
        with any particular specification.

        The comment string should not have a trailing newline.

        The DSC specifies different sections
        in which particular comments can appear.
        This method provides for comments to be emitted
        within three sections:
        the header, the Setup section, and the PageSetup section.
        Comments appearing in the first two sections
        apply to the entire document
        while comments in the BeginPageSetup section
        apply only to a single page.

        For comments to appear in the header section,
        this method should be called after the surface is created,
        but before a call to :meth:`dsc_begin_setup`.

        For comments to appear in the Setup section,
        this method should be called after a call to :meth:`dsc_begin_setup`
        but before a call to :meth:`dsc_begin_page_setup`.

        For comments to appear in the PageSetup section,
        this method should be called after a call to
        :meth:`dsc_begin_page_setup`.

        Note that it is only necessary to call :meth:`dsc_begin_page_setup`
        for the first page of any surface.
        After a call to :meth:`~Surface.show_page`
        or :meth:`~Surface.copy_page`
        comments are unambiguously directed
        to the PageSetup section of the current page.
        But it doesn't hurt to call this method
        at the beginning of every page
        as that consistency may make the calling code simpler.

        As a final note,
        cairo automatically generates several comments on its own.
        As such, applications must not manually generate
        any of the following comments:

        Header section: ``%!PS-Adobe-3.0``, ``%%Creator``, ``%%CreationDate``,
        ``%%Pages``, ``%%BoundingBox``, ``%%DocumentData``,
        ``%%LanguageLevel``, ``%%EndComments``.

        Setup section: ``%%BeginSetup``, ``%%EndSetup``.

        PageSetup section: ``%%BeginPageSetup``, ``%%PageBoundingBox``,
        ``%%EndPageSetup``.

        Other sections: ``%%BeginProlog``, ``%%EndProlog``, ``%%Page``,
        ``%%Trailer``, ``%%EOF``.

        """
        cairo.cairo_ps_surface_dsc_comment(
            self._pointer, _encode_string(comment))
        self._check_status()

    def dsc_begin_setup(self):
        """Indicate that subsequent calls to :meth:`dsc_comment` should
        direct comments to the Setup section of the PostScript output.

        This method should be called at most once per surface,
        and must be called before any call to :meth:`dsc_begin_page_setup`
        and before any drawing is performed to the surface.

        See :meth:`dsc_comment` for more details.

        """
        cairo.cairo_ps_surface_dsc_begin_setup(self._pointer)
        self._check_status()

    def dsc_begin_page_setup(self):
        """Indicate that subsequent calls to :meth:`dsc_comment` should
        direct comments to the PageSetup section of the PostScript output.

        This method is only needed for the first page of a surface.
        It must be called after any call to :meth:`dsc_begin_setup`
        and before any drawing is performed to the surface.

        See :meth:`dsc_comment` for more details.

        """
        cairo.cairo_ps_surface_dsc_begin_page_setup(self._pointer)
        self._check_status()

    def set_eps(self, eps):
        """
        If :obj:`eps` is True,
        the PostScript surface will output Encapsulated PostScript.

        This method should only be called
        before any drawing operations have been performed on the current page.
        The simplest way to do this is to call this method
        immediately after creating the surface.
        An Encapsulated PostScript file should never contain
        more than one page.

        """
        cairo.cairo_ps_surface_set_eps(self._pointer, bool(eps))
        self._check_status()

    def get_eps(self):
        """Check whether the PostScript surface will output
        Encapsulated PostScript.

        """
        return bool(cairo.cairo_ps_surface_get_eps(self._pointer))

    def set_size(self, width_in_points, height_in_points):
        """Changes the size of a PostScript surface
        for the current (and subsequent) pages.

        This method should only be called
        before any drawing operations have been performed on the current page.
        The simplest way to do this is to call this method
        immediately after creating the surface
        or immediately after completing a page with either
        :meth:`~Surface.show_page` or :meth:`~Surface.copy_page`.

        :param width_in_points:
            New width of the page, in points (1 point == 1/72.0 inch)
        :param height_in_points:
            New height of the page, in points (1 point == 1/72.0 inch)
        :type width_in_points: float
        :type height_in_points: float

        """
        cairo.cairo_ps_surface_set_size(
            self._pointer, width_in_points, height_in_points)
        self._check_status()

    def restrict_to_level(self, level):
        """Restricts the generated PostScript file to :obj:`level`.

        See :meth:`get_levels` for a list of available level values
        that can be used here.

        This method should only be called
        before any drawing operations have been performed on the given surface.
        The simplest way to do this is to call this method
        immediately after creating the surface.

        :param version: A :ref:`PS_LEVEL` string.

        """
        cairo.cairo_ps_surface_restrict_to_level(self._pointer, level)
        self._check_status()

    @staticmethod
    def get_levels():
        """Return the list of supported PostScript levels.
        See :meth:`restrict_to_level`.

        :return: A list of :ref:`PS_LEVEL` strings.

        """
        levels = ffi.new('cairo_ps_level_t const **')
        num_levels = ffi.new('int *')
        cairo.cairo_ps_get_levels(levels, num_levels)
        levels = levels[0]
        return [levels[i] for i in range(num_levels[0])]

    @staticmethod
    def ps_level_to_string(level):
        """Return the string representation of the given :ref:`PS_LEVEL`.
        See :meth:`get_levels` for a way to get
        the list of valid level ids.

        """
        c_string = cairo.cairo_ps_level_to_string(level)
        if c_string == ffi.NULL:
            raise ValueError(level)
        return ffi.string(c_string).decode('ascii')


class SVGSurface(Surface):
    """Creates a SVG surface of the specified size in points
    to be written to :obj:`target`.

    :obj:`target` can be :obj:`None` to specify no output.
    This will generate a surface that may be queried and used as a source,
    without generating a temporary file.

    The SVG surface backend recognizes the following MIME types
    for the data attached to a surface (see :meth:`~Surface.set_mime_data`)
    when it is used as a source pattern for drawing on this surface:
    ``image/png``,
    ``image/jpeg`` and
    ``text/x-uri``.
    If any of them is specified, the SVG backend emits a href
    with the content of MIME data instead of a surface snapshot
    (PNG, Base64-encoded) in the corresponding image tag.

    The unofficial MIME type ``text/x-uri`` is examined first.
    If present, the URL is emitted as is:
    assuring the correctness of URL is left to the client code.

    If ``text/x-uri`` is not present,
    but ``image/jpeg`` or ``image/png`` is specified,
    the corresponding data is Base64-encoded and emitted.

    :param target:
        A filename,
        a binary mode file-like object with a :meth:`~file.write` method,
        or :obj:`None`.
    :param width_in_points:
        Width of the surface, in points (1 point == 1/72.0 inch)
    :param height_in_points:
        Height of the surface, in points (1 point == 1/72.0 inch)
    :type width_in_points: float
    :type height_in_points: float

    """
    def __init__(self, target, width_in_points, height_in_points):
        if hasattr(target, 'write') or target is None:
            write_func = _make_write_func(target)
            pointer = cairo.cairo_svg_surface_create_for_stream(
                write_func, ffi.NULL, width_in_points, height_in_points)
        else:
            write_func = None
            pointer = cairo.cairo_svg_surface_create(
                _encode_filename(target), width_in_points, height_in_points)
        Surface.__init__(self, pointer, target_keep_alive=write_func)

    def restrict_to_version(self, version):
        """Restricts the generated SVG file to :obj:`version`.

        See :meth:`get_versions` for a list of available version values
        that can be used here.

        This method should only be called
        before any drawing operations have been performed on the given surface.
        The simplest way to do this is to call this method
        immediately after creating the surface.

        :param version: A :ref:`SVG_VERSION` string.

        """
        cairo.cairo_svg_surface_restrict_to_version(self._pointer, version)
        self._check_status()

    @staticmethod
    def get_versions():
        """Return the list of supported SVG versions.
        See :meth:`restrict_to_version`.

        :return: A list of :ref:`SVG_VERSION` strings.

        """
        versions = ffi.new('cairo_svg_version_t const **')
        num_versions = ffi.new('int *')
        cairo.cairo_svg_get_versions(versions, num_versions)
        versions = versions[0]
        return [versions[i] for i in range(num_versions[0])]

    @staticmethod
    def version_to_string(version):
        """Return the string representation of the given :ref:`SVG_VERSION`.
        See :meth:`get_versions` for a way to get
        the list of valid version ids.

        """
        c_string = cairo.cairo_svg_version_to_string(version)
        if c_string == ffi.NULL:
            raise ValueError(version)
        return ffi.string(c_string).decode('ascii')


class RecordingSurface(Surface):
    """A recording surface is a surface that records all drawing operations
    at the highest level of the surface backend interface,
    (that is, the level of paint, mask, stroke, fill, and show_text_glyphs).
    The recording surface can then be "replayed" against any target surface
    by using it as a source surface.

    If you want to replay a surface so that the results in :obj:`target`
    will be identical to the results that would have been obtained
    if the original operations applied to the recording surface
    had instead been applied to the target surface,
    you can use code like this::

        context = Context(target)
        context.set_source_surface(recording_surface, 0, 0)
        context.paint()

    A recording surface is logically unbounded,
    i.e. it has no implicit constraint on the size of the drawing surface.
    However, in practice this is rarely useful as you wish to replay
    against a particular target surface with known bounds.
    For this case, it is more efficient to specify the target extents
    to the recording surface upon creation.

    The recording phase of the recording surface is careful
    to snapshot all necessary objects (paths, patterns, etc.),
    in order to achieve accurate replay.

    :param content: The :ref:`CONTENT` string of the recording surface
    :param extents:
        The extents to record
        as a ``(x, y, width, height)`` tuple of floats in device units,
        or :obj:`None` to record unbounded operations.
        ``(x, y)`` are the coordinates of the top-left corner of the rectangle,
        ``(width, height)`` its dimensions.

    *New in cairo 1.10*

    *New in cairocffi 0.2*

    """
    def __init__(self, content, extents):
        extents = (ffi.new('cairo_rectangle_t *', extents)
                   if extents is not None else ffi.NULL)
        Surface.__init__(
            self, cairo.cairo_recording_surface_create(content, extents))

    def get_extents(self):
        """Return the extents of the recording-surface.

        :returns:
            A ``(x, y, width, height)`` tuple of floats,
            or :obj:`None` if the surface is unbounded.

        *New in cairo 1.12*

        """
        extents = ffi.new('cairo_rectangle_t *')
        if cairo.cairo_recording_surface_get_extents(self._pointer, extents):
            return (extents.x, extents.y, extents.width, extents.height)

    def ink_extents(self):
        """Measures the extents of the operations
        stored within the recording-surface.
        This is useful to compute the required size of an image surface
        (or equivalent) into which to replay the full sequence
        of drawing operations.

        :return: A ``(x, y, width, height)`` tuple of floats.

        """
        extents = ffi.new('double[4]')
        cairo.cairo_recording_surface_ink_extents(
            self._pointer, extents + 0, extents + 1, extents + 2, extents + 3)
        self._check_status()
        return tuple(extents)


SURFACE_TYPE_TO_CLASS = {
    'IMAGE': ImageSurface,
    'PDF': PDFSurface,
    'SVG': SVGSurface,
    'RECORDING': RecordingSurface,
}

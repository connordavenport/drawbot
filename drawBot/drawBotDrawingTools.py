import math
import os
import random
from collections import namedtuple
from contextlib import contextmanager
from typing import Any

import AppKit  # type: ignore
import CoreText  # type: ignore
import Quartz  # type: ignore

from .aliases import (
    BoundingBox,
    CMYKColor,
    CMYKColorTuple,
    Point,
    RGBAColorTuple,
    RGBColor,
    RGBColorTuple,
    Size,
    SomePath,
    TransformTuple,
)
from .context import getContextForFileExt, getContextOptionsDocs, getFileExtensions
from .context.baseContext import (
    BezierPath,
    FormattedString,
    getFontName,
    getNSFontFromNameOrPath,
    makeTextBoxes,
    newFramesetterWithAttributedString,
)
from .context.dummyContext import DummyContext
from .context.tools import drawBotbuiltins, gifTools
from .context.tools.imageObject import ImageObject
from .misc import (
    DrawBotError,
    VariableController,
    clearMemoizeCache,
    isEPS,
    isGIF,
    isPDF,
    optimizePath,
    transformationAtCenter,
    warnings,
)


def _getmodulecontents(module, names=None):
    d = {}
    if names is None:
        names = [name for name in dir(module) if not name.startswith("_")]
    for name in names:
        d[name] = getattr(module, name)
    return d


_paperSizes = {
    "Letter": (612, 792),
    "LetterSmall": (612, 792),
    "Tabloid": (792, 1224),
    "Ledger": (1224, 792),
    "Legal": (612, 1008),
    "Statement": (396, 612),
    "Executive": (540, 720),
    "A0": (2384, 3371),
    "A1": (1685, 2384),
    "A2": (1190, 1684),
    "A3": (842, 1190),
    "A4": (595, 842),
    "A4Small": (595, 842),
    "A5": (420, 595),
    "B4": (729, 1032),
    "B5": (516, 729),
    "Folio": (612, 936),
    "Quarto": (610, 780),
    "10x14": (720, 1008),
}

for key, (w, h) in list(_paperSizes.items()):
    _paperSizes["%sLandscape" % key] = (h, w)


class DrawBotDrawingTool:
    def __init__(self):
        self._reset()
        self._isSinglePage = False

    def _get__all__(self):
        return [i for i in dir(self) if not i.startswith("_")] + ["__version__"]

    __all__ = property(_get__all__)

    def _get_version(self):
        try:
            from drawBot import drawBotSettings

            return drawBotSettings.__version__
        except Exception:
            pass
        return ""

    __version__ = property(_get_version)

    def _addToNamespace(self, namespace):
        namespace.update(_getmodulecontents(self, self.__all__))
        namespace.update(_getmodulecontents(random, ["random", "randint", "choice", "shuffle"]))
        namespace.update(_getmodulecontents(math))
        namespace.update(_getmodulecontents(drawBotbuiltins))
        namespace["FormattedString"] = FormattedString
        namespace["BezierPath"] = BezierPath
        namespace["ImageObject"] = ImageObject

    def _addInstruction(self, callback, *args, **kwargs):
        if callback == "newPage":
            self._instructionsStack.append([])
        if not self._instructionsStack:
            self._instructionsStack.append([])
        if self._requiresNewFirstPage and not self._hasPage:
            self._hasPage = True
            self._instructionsStack[-1].insert(0, ("newPage", [self.width(), self.height()], {}))
        self._instructionsStack[-1].append((callback, args, kwargs))

    def _drawInContext(self, context):
        if not self._instructionsStack:
            return
        for instructionSet in self._instructionsStack:
            for callback, args, kwargs in instructionSet:
                attr = getattr(context, callback)
                attr(*args, **kwargs)

    def _reset(self, other=None):
        if other is not None:
            self._instructionsStack = list(other._instructionsStack)
            self._dummyContext = other._dummyContext
            self._width = other._width
            self._height = other._height
            self._tempInstalledFonts = dict(other._tempInstalledFonts)
            self._requiresNewFirstPage = other._requiresNewFirstPage
            self._hasPage = other._hasPage
        else:
            self._instructionsStack = []
            self._dummyContext = DummyContext()
            self._width = None
            self._height = None
            self._requiresNewFirstPage = False
            self._hasPage = False
            if not hasattr(self, "_tempInstalledFonts"):
                self._tempInstalledFonts = dict()
        self._cachedPixelColorBitmaps = {}
        clearMemoizeCache()

    def _copy(self):
        new = self.__class__()
        new._instructionsStack = list(self._instructionsStack)
        new._dummyContext = self._dummyContext
        new._width = self._width
        new._height = self._height
        new._hasPage = self._hasPage
        new._requiresNewFirstPage = self._requiresNewFirstPage
        new._tempInstalledFonts = dict(self._tempInstalledFonts)
        return new

    def newDrawing(self):
        """
        Reset the drawing stack to the clean and empty stack.

        .. downloadcode:: newDrawing.py

            # draw a rectangle
            rect(10, 10, width()-20, height()-20)
            # save it as a pdf
            saveImage("~/Desktop/aRect.pdf")

            # reset the drawing stack to a clear and empty stack
            newDrawing()

            # draw an oval
            oval(10, 10, width()-20, height()-20)
            # save it as a pdf
            saveImage("~/Desktop/anOval.pdf")
        """
        self._reset()

    def endDrawing(self):
        """
        Explicitly tell drawBot the drawing is done.
        This is advised when using drawBot as a standalone module.
        """
        self._uninstallAllFonts()
        gifTools.clearExplodedGifCache()

    @contextmanager
    def drawing(self):
        """
        Reset and clean the drawing stack in a `with` statement.

        .. downloadcode:: drawing.py

            # Use the 'with' statement.
            # This will make sure that the stack of pages is cleaned and reset
            # once the interpreter exits the `with` statement
            # The following example shows how to create three PDF booklets
            # and it uses the `with drawing()` statement to ensure that page numbers
            # restart from 1 in each PDF
            for eachBooklet in range(1, 4):
                with drawing():
                    for eachPage in range(10):
                        newPage(100, 100)
                        text(f"{pageCount()}", (40, 40))
                    saveImage(f"book_{eachBooklet}.pdf")
        """
        self.newDrawing()
        try:
            yield
        finally:
            self.endDrawing()
            self.newDrawing()

    # magic variables

    def width(self) -> float:
        """
        Returns the width of the current page.
        """
        if self._width is None:
            return 1000
        return self._width

    def height(self) -> float:
        """
        Returns the height of the current page.
        """
        if self._height is None:
            return 1000
        return self._height

    def sizes(self, paperSize: str | None = None) -> Size | dict[str, Size]:
        """
        Returns the width and height of a specified canvas size.
        If no canvas size is given it will return the dictionary containing all possible page sizes.
        """
        w, h = AppKit.NSScreen.mainScreen().frame().size
        _paperSizes["screen"] = int(w), int(h)
        if paperSize:
            return _paperSizes[paperSize]
        return _paperSizes

    def pageCount(self) -> int:
        """
        Returns the current page count.
        """
        return len(self._instructionsStack)

    # ====================
    # = public callbacks =
    # ====================

    # size and pages

    def size(self, width: float | str, height: float | None = None):
        """
        Set the width and height of the canvas.
        Without calling `size()` the default drawing board is 1000 by 1000 points.

        Alternatively `size('A4')` with a supported papersizes or `size('screen')` setting the current screen size as size, can be used.

        Afterwards the functions `width()` and `height()` can be used for calculations.

        You have to use `size()` before any drawing-related code, and you can't use `size()`
        in a multi-page document. Use `newPage(w, h)` to set the correct dimensions for each page.

        .. downloadcode:: size.py

            # set a canvas size
            size(200, 200)
            # print out the size of the page
            print((width(), height()))

            # set a color
            fill(1, 0, 0)
            # use those variables to set a background color
            rect(0, 0, width(), height())

        All supported papersizes: 10x14, 10x14Landscape, A0, A0Landscape, A1, A1Landscape, A2, A2Landscape, A3, A3Landscape, A4, A4Landscape, A4Small, A4SmallLandscape, A5, A5Landscape, B4, B4Landscape, B5, B5Landscape, Executive, ExecutiveLandscape, Folio, FolioLandscape, Ledger, LedgerLandscape, Legal, LegalLandscape, Letter, LetterLandscape, LetterSmall, LetterSmallLandscape, Quarto, QuartoLandscape, Statement, StatementLandscape, Tabloid, TabloidLandscape.
        """
        if self._isSinglePage:
            # dont allow to set a page size
            raise DrawBotError("Cannot set 'size' into a single page.")
        if width in _paperSizes and isinstance(width, str):
            width, height = _paperSizes[width]
        if width == "screen":
            width, height = AppKit.NSScreen.mainScreen().frame().size
        if height is None and isinstance(width, float):
            width, height = width, width
        self._width = width
        self._height = height
        if not self._instructionsStack:
            self.newPage(width, height)
        else:
            raise DrawBotError("Can't use 'size()' after drawing has begun. Try to move it to the top of your script.")

    def newPage(self, width: str | float | None = None, height: float | None = None):
        """
        Create a new canvas to draw in.
        This will act like a page in a pdf or a frame in a mov.

        Optionally a `width` and `height` argument can be provided to set the size.
        If not provided the default size will be used.

        Alternatively `size('A4')` with a supported papersizes or `size('screen')` setting the current screen size as size, can be used.

        .. downloadcode:: newPage.py

            # loop over a range of 100
            for i in range(100):
                # for each loop create a new path
                newPage(500, 500)
                # set a random fill color
                fill(random(), random(), random())
                # draw a rect with the size of the page
                rect(0, 0, width(), height())

        All supported papersizes: 10x14, 10x14Landscape, A0, A0Landscape, A1, A1Landscape, A2, A2Landscape, A3, A3Landscape, A4, A4Landscape, A4Small, A4SmallLandscape, A5, A5Landscape, B4, B4Landscape, B5, B5Landscape, Executive, ExecutiveLandscape, Folio, FolioLandscape, Ledger, LedgerLandscape, Legal, LegalLandscape, Letter, LetterLandscape, LetterSmall, LetterSmallLandscape, Quarto, QuartoLandscape, Statement, StatementLandscape, Tabloid, TabloidLandscape.
        """
        if self._isSinglePage:
            # dont allow to add a page
            raise DrawBotError("Cannot add a 'newPage' into a single page.")
        if width in _paperSizes and isinstance(width, str):
            width, height = _paperSizes[width]
        if width == "screen":
            width, height = AppKit.NSScreen.mainScreen().frame().size
        if width is None and height is None:
            width = self.width()
            height = self.height()
        self._width = width
        self._height = height
        self._hasPage = True
        self._dummyContext = DummyContext()
        self._addInstruction("newPage", width, height)

    def pages(self):
        """
        Return all pages.

        .. downloadcode:: pages.py

            # set a size
            size(200, 200)
            # draw a rectangle
            rect(10, 10, 100, 100)
            # create a new page
            newPage(200, 300)
            # set a color
            fill(1, 0, 1)
            # draw a rectangle
            rect(10, 10, 100, 100)
            # create a new page
            newPage(200, 200)
            # set a color
            fill(0, 1, 0)
            # draw a rectangle
            rect(10, 10, 100, 100)

            # get all pages
            allPages = pages()
            # count how many pages are available
            print(len(allPages))

            # use the `with` statement
            # to set a page as current context
            with allPages[1]:
                # draw into the selected page
                fontSize(30)
                text("Hello World", (10, 150))

            # loop over allpages
            for page in allPages:
                # set the page as current context
                with page:
                    # draw an oval in each of them
                    oval(110, 10, 30, 30)
        """
        from .drawBotPageDrawingTools import DrawBotPage

        instructions = []
        for instructionSet in self._instructionsStack:
            for callback, _, _ in instructionSet:
                if callback == "newPage":
                    instructions.append(instructionSet)
                    break
        return tuple(DrawBotPage(instructionSet) for instructionSet in instructions)

    def saveImage(self, path: SomePath, *args, **options: dict[str, Any]):
        """
        Save or export the canvas to a specified format.
        The `path` argument is a single destination path to save the current drawing actions.

        The file extension is important because it will determine the format in which the image will be exported.

        All supported file extensions: %(supportedExtensions)s.
        (`*` will print out all actions.)

        When exporting an animation or movie, each page represents a frame and the framerate is set by calling `frameDuration()` after each `newPage()`.

        .. downloadcode:: saveImage.py

            # set the canvas size
            size(150, 100)

            # draw a background
            rect(10, 10, width()-20, height()-20)

            # set a fill
            fill(1)
            # draw some text
            text("Hello World!", (20, 40))
            # save it as a png and pdf on the current users desktop
            saveImage("~/Desktop/firstImage.png")
            saveImage("~/Desktop/firstImage.pdf")

        `saveImage()` options can be set by adding keyword arguments. Which options are recognized
        depends on the output format.

        %(supportedOptions)s

        .. downloadcode:: saveImageResolutionExample.py

            # same example but we just change the image resolution
            size(150, 100)
            rect(10, 10, width()-20, height()-20)
            fill(1)
            text("Hello World!", (20, 40))
            # save it with an option that controls the resolution (300 PPI)
            saveImage("~/Desktop/firstImage300.png", imageResolution=300)

        """
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError(
                "Cannot apply saveImage options to multiple output formats, expected 'str' or 'os.PathLike', got '%s'"
                % type(path).__name__
            )
        # args are not supported anymore
        if args:
            if len(args) == 1:
                # if there is only 1 is the old multipage
                warnings.warn("'multipage' should be a keyword argument: use 'saveImage(path, multipage=True)'")
                options["multipage"] = args[0]
            else:
                # if there are more just raise a TypeError
                raise TypeError("saveImage(path, **options) takes only keyword arguments")
        originalPath = path
        path = optimizePath(path)
        dirName = os.path.dirname(path)
        if not os.path.exists(dirName):
            raise DrawBotError("Folder '%s' doesn't exists" % dirName)
        base, ext = os.path.splitext(path)
        ext = ext.lower()[1:]
        if not ext:
            path = ext = originalPath
        context = getContextForFileExt(ext)
        if context is None:
            raise DrawBotError("Could not find a supported context for: '%s'" % ext)
        if context.validateSaveImageOptions:
            allowedSaveImageOptions = set(optionName for optionName, optionDoc in context.saveImageOptions)
            for optionName in options:
                if optionName not in allowedSaveImageOptions:
                    warnings.warn(
                        "Unrecognized saveImage() option found for %s: %s" % (context.__class__.__name__, optionName)
                    )
        self._drawInContext(context)
        return context.saveImage(path, options)

    # filling docs with content from all possible and installed contexts
    saveImage.__doc__ = saveImage.__doc__ % dict(  # type: ignore
        supportedExtensions="`%s`" % "`, `".join(getFileExtensions()),
        supportedOptions="\n        ".join(getContextOptionsDocs()),
    )

    def printImage(self, pdf=None):
        """
        Export the canvas to a printing dialog, ready to print.

        Optionally a `pdf` object can be provided.

        .. downloadcode:: printImage.py

            # set A4 page size
            size(595, 842)
            # draw something
            oval(0, 0, width(), height())
            # send it to the printer
            printImage()
        """
        context = getContextForFileExt("pdf")
        if pdf is None:
            self._drawInContext(context)
            context.printImage()
        else:
            context.printImage(pdf)

    def pdfImage(self):
        """
        Return the image as a pdf document object.
        """
        from .context.drawBotContext import DrawBotContext

        context = DrawBotContext()
        self._drawInContext(context)
        return context.getNSPDFDocument()

    # graphics state

    def save(self):
        """
        DrawBot strongly recommends to use `savedState()` in a `with` statement instead.

        Save the current graphics state.
        This will save the state of the canvas (with all the transformations)
        but also the state of the colors, strokes...
        """
        self._dummyContext.save()
        self._requiresNewFirstPage = True
        self._addInstruction("save")

    def restore(self):
        """
        DrawBot strongly recommends to use `savedState()` in a `with` statement instead.

        Restore from a previously saved graphics state.
        This will restore the state of the canvas (with all the transformations)
        but also the state of colors, strokes...
        """
        self._dummyContext.restore()
        self._requiresNewFirstPage = True
        self._addInstruction("restore")

    @contextmanager
    def savedState(self):
        """
        Save and restore the current graphics state in a `with` statement.

        .. downloadcode:: savedState.py

            # Use the 'with' statement.
            # This makes any changes you make to the graphics state -- such as
            # colors and transformations -- temporary, and will be reset to
            # the previous state at the end of the 'with' block.
            with savedState():
                # set a color
                fill(1, 0, 0)
                # do a transformation
                translate(450, 50)
                rotate(45)
                # draw something
                rect(0, 0, 700, 600)
            # already returned to the previously saved graphics state
            # so this will be a black rectangle
            rect(0, 0, 50, 50)
        """
        self.save()
        try:
            yield
        finally:
            self.restore()

    # basic shapes

    def rect(self, x: float, y: float, w: float, h: float):
        """
        Draw a rectangle from position x, y with the given width and height.

        .. downloadcode:: rect.py

            # draw a rectangle
            #    x    y    w    h
            rect(100, 100, 800, 800)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("rect", x, y, w, h)

    def oval(self, x: float, y: float, w: float, h: float):
        """
        Draw an oval from position x, y with the given width and height.

        .. downloadcode:: oval.py

            # draw an oval
            #    x    y    w    h
            oval(100, 100, 800, 800)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("oval", x, y, w, h)

    # path

    def newPath(self):
        """
        Create a new path.
        """
        self._requiresNewFirstPage = True
        self._addInstruction("newPath")

    def moveTo(self, xy: Point):
        """
        Move to a point `x`, `y`.
        """
        x, y = xy
        self._requiresNewFirstPage = True
        self._addInstruction("moveTo", (x, y))

    def lineTo(self, xy: Point):
        """
        Line to a point `x`, `y`.
        """
        x, y = xy
        self._requiresNewFirstPage = True
        self._addInstruction("lineTo", (x, y))

    def curveTo(self, xy1: Point, xy2: Point, xy3: Point):
        """
        Curve to a point `x3`, `y3`.
        With given bezier handles `x1`, `y1` and `x2`, `y2`.
        """
        x1, y1 = xy1
        x2, y2 = xy2
        x3, y3 = xy3
        self._requiresNewFirstPage = True
        self._addInstruction("curveTo", (x1, y1), (x2, y2), (x3, y3))

    def qCurveTo(self, *points: Point):
        """
        Quadratic curve with a given set of off curves to a on curve.
        """
        self._requiresNewFirstPage = True
        self._addInstruction("qCurveTo", points)

    def arc(
        self,
        center: Point,
        radius: float,
        startAngle: float,
        endAngle: float,
        clockwise: bool,
    ):
        """
        Arc with `center` and a given `radius`, from `startAngle` to `endAngle`, going clockwise if `clockwise` is True and counter clockwise if `clockwise` is False.
        """
        self._requiresNewFirstPage = True
        self._addInstruction("arc", center, radius, startAngle, endAngle, clockwise)

    def arcTo(self, xy1: Point, xy2: Point, radius: float):
        """
        Arc from one point to an other point with a given `radius`.

        .. downloadcode:: arcTo-example.py

            pt0 = 74, 48
            pt1 = 238, 182
            pt2 = 46, 252
            radius = 60

            def drawPt(pos, r=5):
                x, y = pos
                oval(x-r, y-r, r*2, r*2)

            size(300, 300)
            fill(None)

            path = BezierPath()
            path.moveTo(pt0)
            path.arcTo(pt1, pt2, radius)

            stroke(0, 1, 1)
            polygon(pt0, pt1, pt2)
            for pt in [pt0, pt1, pt2]:
                drawPt(pt)

            stroke(0, 0, 1)
            drawPath(path)
            stroke(1, 0, 1)
            for pt in path.onCurvePoints:
                drawPt(pt, r=3)
            for pt in path.offCurvePoints:
                drawPt(pt, r=2)
        """
        x1, y1 = xy1
        x2, y2 = xy2
        self._requiresNewFirstPage = True
        self._addInstruction("arcTo", (x1, y1), (x2, y2), radius)

    def closePath(self):
        """
        Close the path.
        """
        self._requiresNewFirstPage = True
        self._addInstruction("closePath")

    def drawPath(self, path: BezierPath | None = None):
        """
        Draw the current path, or draw the provided path.

        .. downloadcode:: drawPath.py

            # create a new empty path
            newPath()
            # set the first oncurve point
            moveTo((100, 100))
            # line to from the previous point to a new point
            lineTo((100, 900))
            lineTo((900, 900))

            # curve to a point with two given handles
            curveTo((900, 500), (500, 100), (100, 100))

            # close the path
            closePath()
            # draw the path
            drawPath()
        """
        if isinstance(path, AppKit.NSBezierPath):
            path = BezierPath(path)
        if isinstance(path, BezierPath):
            path = path.copy()
        self._requiresNewFirstPage = True
        self._addInstruction("drawPath", path)

    def clipPath(self, path=None):
        """
        Use the given path as a clipping path, or the current path if no path was given.

        Everything drawn after a `clipPath()` call will be clipped by the clipping path.
        To "undo" the clipping later, make sure you do the clipping inside a
        `with savedState():` block, as shown in the example.

        .. downloadcode:: clipPath.py

            # create a bezier path
            path = BezierPath()
            # draw a triangle
            # move to a point
            path.moveTo((100, 100))
            # line to a point
            path.lineTo((100, 900))
            path.lineTo((900, 900))
            # close the path
            path.closePath()
            # save the graphics state so the clipping happens only
            # temporarily
            with savedState():
                # set the path as a clipping path
                clipPath(path)
                # the oval will be clipped inside the path
                oval(100, 100, 800, 800)
            # no more clipping here
        """
        self._requiresNewFirstPage = True
        self._addInstruction("clipPath", path)

    def line(self, point1: Point, point2: Point):
        """
        Draws a line between two given points.

        .. downloadcode:: line.py

            # set a stroke color
            stroke(0)
            # draw a line between two given points
            line((100, 100), (900, 900))
        """
        path = BezierPath()
        path.line(point1, point2)
        self.drawPath(path)

    def polygon(self, *points: Point, **kwargs: bool):
        """
        Draws a polygon with n-amount of points.
        Optionally a `close` argument can be provided to open or close the path.
        As default a `polygon` is a closed path.

        .. downloadcode:: polygon.py

            # draw a polygon with x-amount of points
            polygon((100, 100), (100, 900), (900, 900), (200, 800), close=True)
        """
        path = BezierPath()
        path.polygon(*points, **kwargs)
        self.drawPath(path)

    # color

    def colorSpace(self, colorSpace):
        """
        Set the color space.
        Options are `genericRGB`, `adobeRGB1998`, `sRGB`, `genericGray`, `genericGamma22Gray`, `displayP3`.
        The default is `genericRGB`.
        `None` will reset it back to the default.

        .. downloadcode:: colorSpace.py

            # set a color
            r, g, b, a = 0.74, 0.51, 1.04, 1

            # get all available color spaces
            colorSpaces = listColorSpaces()

            x = 0
            w = width() / len(colorSpaces)

            # start loop
            for space in colorSpaces:

                # set a color space
                colorSpace(space)
                # set the color
                fill(r, g, b)
                # draw a rect
                rect(x, 0, w, height())
                x += w
        """
        self._requiresNewFirstPage = True
        self._addInstruction("colorSpace", colorSpace)

    def listColorSpaces(self) -> list[str]:
        """
        Return a list of all available color spaces.
        """
        return sorted(self._dummyContext._colorSpaceMap.keys())

    def blendMode(self, operation: str):
        """
        Set a blend mode.

        Available operations are: `normal`, `multiply`, `screen`, `overlay`,
        `darken`, `lighten`, `colorDodge`, `colorBurn`, `softLight`,
        `hardLight`, `difference`, `exclusion`, `hue`, `saturation`,
        `color`, `luminosity`, `clear`, `copy`, `sourceIn`, `sourceOut`,
        `sourceAtop`, `destinationOver`, `destinationIn`, `destinationOut`,
        `destinationAtop`, `xOR`, `plusDarker` and `plusLighter`,

        .. downloadcode:: blendMode.py

            # set a blend mode
            blendMode("multiply")
            # set a color
            cmykFill(1, 0, 0, 0)
            # draw a rectangle
            rect(10, 10, 600, 600)
            # set an other color
            cmykFill(0, 1, 0, 0)
            # overlap a second rectangle
            rect(390, 390, 600, 600)

        """
        if operation not in self._dummyContext._blendModeMap.keys():
            raise DrawBotError("blend mode must be %s" % (", ".join(self._dummyContext._blendModeMap.keys())))
        self._requiresNewFirstPage = True
        self._addInstruction("blendMode", operation)

    def fill(
        self,
        r: float | None = None,
        g: float | None = None,
        b: float | None = None,
        alpha: float = 1,
    ):
        """
        Sets the fill color with a `red`, `green`, `blue` and `alpha` value.
        Each argument must a value float between 0 and 1.

        .. downloadcode:: fill.py

            fill(1, 0, 0, .5)
            # draw a rect
            rect(10, 10, 200, 980)

            # only set a gray value
            fill(0)
            # draw a rect
            rect(200, 10, 200, 980)

            # only set a gray value with an alpha
            fill(0, .5)
            # draw a rect
            rect(400, 10, 200, 980)

            # set rgb with no alpha
            fill(1, 0, 0)
            # draw a rect
            rect(600, 10, 200, 980)

            # set rgb with an alpha value
            fill(1, 0, 0, .5)
            # draw a rect
            rect(800, 10, 190, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("fill", r, g, b, alpha)

    def stroke(
        self,
        r: float | None = None,
        g: float | None = None,
        b: float | None = None,
        alpha: float = 1,
    ):
        """
        Sets the stroke color with a `red`, `green`, `blue` and `alpha` value.
        Each argument must a value float between 0 and 1.

        .. downloadcode:: stroke.py

            # set the fill to none
            fill(None)
            # set a stroke width
            stroke(1, 0, 0, .3)
            strokeWidth(10)
            # draw a rect
            rect(10, 10, 180, 980)

            # only set a gray value
            stroke(0)
            # draw a rect
            rect(210, 10, 180, 980)

            # only set a gray value with an alpha
            stroke(0, .5)
            # draw a rect
            rect(410, 10, 180, 980)

            # set rgb with no alpha
            stroke(1, 0, 0)
            # draw a rect
            rect(610, 10, 180, 980)

            # set rgb with an alpha value
            stroke(1, 0, 0, .5)
            # draw a rect
            rect(810, 10, 180, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("stroke", r, g, b, alpha)

    def cmykFill(
        self,
        c: float | None,
        m: float | None = None,
        y: float | None = None,
        k: float | None = None,
        alpha: float = 1,
    ):
        """
        Set a fill using a CMYK color before drawing a shape. This is handy if the file is intended for print.

        Sets the CMYK fill color. Each value must be a float between 0.0 and 1.0.

        .. downloadcode:: cmykFill.py

            # cyan
            cmykFill(1, 0, 0, 0)
            rect(0, 0, 250, 1000)
            # magenta
            cmykFill(0, 1, 0, 0)
            rect(250, 0, 250, 1000)
            # yellow
            cmykFill(0, 0, 1, 0)
            rect(500, 0, 250, 1000)
            # black
            cmykFill(0, 0, 0, 1)
            rect(750, 0, 250, 1000)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("cmykFill", c, m, y, k, alpha)

    def cmykStroke(
        self,
        c: float | None,
        m: float | None = None,
        y: float | None = None,
        k: float | None = None,
        alpha: float = 1,
    ):
        """
        Set a stroke using a CMYK color before drawing a shape. This is handy if the file is intended for print.

        Sets the CMYK stroke color. Each value must be a float between 0.0 and 1.0.

        .. downloadcode:: cmykStroke.py

            # define x, y and the amount of lines needed
            x, y = 20, 20
            lines = 49
            # calculate the smallest step
            colorStep = 1.00 / lines
            # set stroke width
            strokeWidth(10)
            # start a loop
            for i in range(lines):
                # set a cmyk color
                # the magenta value is calculated
                cmykStroke(0, i * colorStep, 1, 0)
                # draw a line
                line((x, y), (x, y + 960))
                # translate the canvas
                translate(20, 0)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("cmykStroke", c, m, y, k, alpha)

    def opacity(self, value: float):
        """
        Sets the current opacity value. The `value` argument must be a value between 0.0 and 1.0.

        .. downloadcode:: opacity.py

            # set an opacity value
            opacity(.5)
            # set a color and draw some rect and text
            fill(1, 0, 0)
            rect(10, 10, 600, 600)
            fill(0, 1, 0)
            rect(390, 390, 600, 600)
            fontSize(400)
            fill(0, 0, 1)
            text("draw", (500, 500), align="center")
        """
        self._requiresNewFirstPage = True
        self._addInstruction("opacity", value)

    def shadow(
        self,
        offset: Point,
        blur: float | None = None,
        color: tuple[float, ...] | None = None,
    ):
        """
        Adds a shadow with an `offset` (x, y), `blur` and a `color`.
        The `color` argument must be a tuple similarly as `fill`.
        The `offset`and `blur` argument will be drawn independent of the current context transformations.

        .. downloadcode:: shadow.py

            # a red shadow with some blur and a offset
            shadow((100, 100), 100, (1, 0, 0))
            # draw a rect
            rect(100, 100, 600, 600)
        """
        if color is None:
            color = (0, 0, 0)
        if blur is None:
            blur = 10
        self._requiresNewFirstPage = True
        self._addInstruction("shadow", offset, blur, color)

    def cmykShadow(
        self,
        offset: Point,
        blur: float | None = None,
        color: tuple[float, ...] | None = None,
    ):
        """
        Adds a cmyk shadow with an `offset` (x, y), `blur` and a `color`.
        The `color` argument must be a tuple similarly as `cmykFill`.

        .. downloadcode:: cmykShadow.py

            # a cyan with some blur and a offset
            cmykShadow((100, 100), 100, (1, 0, 0, 0))
            # draw a rect
            rect(100, 100, 600, 600)
        """
        if color is None:
            color = (0, 0, 0, 1, 1)
        if blur is None:
            blur = 10
        self._requiresNewFirstPage = True
        self._addInstruction("cmykShadow", offset, blur, color)

    def linearGradient(
        self,
        startPoint: Point | None = None,
        endPoint: Point | None = None,
        colors: list[RGBColor | RGBColorTuple] | None = None,
        locations: list[float] | None = None,
    ):
        """
        A linear gradient fill with:

        * `startPoint` as (x, y)
        * `endPoint` as (x, y)
        * `colors` as a list of colors, tuples of floating point values (0 → 1)
        * `locations` of each color as a list of floats. (optionally)

        Setting a gradient will ignore the `fill`.

        .. downloadcode:: linearGradient.py

            # set a gradient as the fill color
            linearGradient(
                (100, 100),                         # startPoint
                (800, 800),                         # endPoint
                [(1, 0, 0), (0, 0, 1), (0, 1, 0)],  # colors
                [0, .2, 1]                          # locations
                )
            # draw a rectangle
            rect(10, 10, 980, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("linearGradient", startPoint, endPoint, colors, locations)

    def cmykLinearGradient(
        self,
        startPoint: Point | None = None,
        endPoint: Point | None = None,
        colors: list[CMYKColorTuple] | None = None,
        locations=None,
    ):
        """
        A cmyk linear gradient fill with:

        * `startPoint` as (x, y)
        * `endPoint` as (x, y)
        * `colors` as a list of colors, tuples of floating point values (0 → 1)
        * `locations` of each color as a list of floats. (optionally)

        Setting a gradient will ignore the `fill`.

        .. downloadcode:: cmykLinearGradient.py

            # set a gradient as the fill color
            cmykLinearGradient(
                (100, 100),                                  # startPoint
                (800, 800),                                  # endPoint
                [(1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0)],  # colors
                [0, .2, 1]                                   # locations
                )
            # draw a rectangle
            rect(10, 10, 980, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("cmykLinearGradient", startPoint, endPoint, colors, locations)

    def radialGradient(
        self,
        startPoint: Point | None = None,
        endPoint: Point | None = None,
        colors: list[RGBColor] | None = None,
        locations: list[float] | None = None,
        startRadius: float = 0,
        endRadius: float = 100,
    ):
        """
        A radial gradient fill with:

        * `startPoint` as (x, y)
        * `endPoint` as (x, y)
        * `colors` as a list of colors, described similary as `fill`
        * `locations` of each color as a list of floats. (optionally)
        * `startRadius` radius around the startPoint in degrees (optionally)
        * `endRadius` radius around the endPoint in degrees (optionally)

        Setting a gradient will ignore the `fill`.

        .. downloadcode:: radialGradient.py

            # set a gradient as the fill color
            radialGradient(
                (300, 300),                         # startPoint
                (600, 600),                         # endPoint
                [(1, 0, 0), (0, 0, 1), (0, 1, 0)],  # colors
                [0, .2, 1],                         # locations
                0,                                  # startRadius
                500                                 # endRadius
                )
            # draw a rectangle
            rect(10, 10, 980, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("radialGradient", startPoint, endPoint, colors, locations, startRadius, endRadius)

    def cmykRadialGradient(
        self,
        startPoint: Point | None = None,
        endPoint: Point | None = None,
        colors: list[CMYKColor] | None = None,
        locations: list[float] | None = None,
        startRadius: float = 0,
        endRadius: float = 100,
    ):
        """
        A cmyk radial gradient fill with:

        * `startPoint` as (x, y)
        * `endPoint` as (x, y)
        * `colors` as a list of colors, described similary as `cmykFill`
        * `locations` of each color as a list of floats. (optionally)
        * `startRadius` radius around the startPoint in degrees (optionally)
        * `endRadius` radius around the endPoint in degrees (optionally)

        Setting a gradient will ignore the `fill`.

        .. downloadcode:: cmykRadialGradient.py

            # set a gradient as the fill color
            cmykRadialGradient(
                (300, 300),                                     # startPoint
                (600, 600),                                     # endPoint
                [(1, 0, 0, 1), (0, 0, 1, 0), (0, 1, 0, .2)],    # colors
                [0, .2, 1],                                     # locations
                0,                                              # startRadius
                500                                             # endRadius
                )
            # draw a rectangle
            rect(10, 10, 980, 980)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("cmykRadialGradient", startPoint, endPoint, colors, locations, startRadius, endRadius)

    # path drawing behavoir

    def strokeWidth(self, value: float):
        """
        Sets stroke width.

        .. downloadcode:: strokeWidth.py

            # set no fill
            fill(None)
            # set black as the stroke color
            stroke(0)
            # loop over a range of 10
            for i in range(20):
                # in each loop set the stroke width
                strokeWidth(i)
                # draw a line
                line((100, 100), (200, 900))
                # and translate the canvas
                translate(30, 0)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("strokeWidth", value)

    def miterLimit(self, value: float):
        """
        Set a miter limit. Used on corner points.

        .. downloadcode:: miterLimit.py

            # create a path
            path = BezierPath()
            # move to a point
            path.moveTo((100, 100))
            # line to a point
            path.lineTo((150, 700))
            path.lineTo((300, 100))
            # set stroke color to black
            stroke(0)
            # set no fill
            fill(None)
            # set the width of the stroke
            strokeWidth(50)
            # draw the path
            drawPath(path)
            # move the canvas
            translate(500, 0)
            # set a miter limit
            miterLimit(5)
            # draw the same path again
            drawPath(path)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("miterLimit", value)

    def lineJoin(self, value: str):
        """
        Set a line join.

        Possible values are `miter`, `round` and `bevel`.

        .. downloadcode:: lineJoin.py

            # set the stroke color to black
            stroke(0)
            # set no fill
            fill(None)
            # set a stroke width
            strokeWidth(30)
            # set a miter limit
            miterLimit(30)
            # create a bezier path
            path = BezierPath()
            # move to a point
            path.moveTo((100, 100))
            # line to a point
            path.lineTo((100, 600))
            path.lineTo((160, 100))
            # set a line join style
            lineJoin("miter")
            # draw the path
            drawPath(path)
            # translate the canvas
            translate(300, 0)
            # set a line join style
            lineJoin("round")
            # draw the path
            drawPath(path)
            # translate the canvas
            translate(300, 0)
            # set a line join style
            lineJoin("bevel")
            # draw the path
            drawPath(path)
        """
        self._requiresNewFirstPage = True
        self._addInstruction("lineJoin", value)

    def lineCap(self, value: str):
        """
        Set a line cap.

        Possible values are `butt`, `square` and `round`.

        .. downloadcode:: lineCap.py

            # set stroke color to black
            stroke(0)
            # set a strok width
            strokeWidth(50)
            # translate the canvas
            translate(150, 50)
            # set a line cap style
            lineCap("butt")
            # draw a line
            line((0, 200), (0, 800))
            # translate the canvas
            translate(300, 0)
            # set a line cap style
            lineCap("square")
            # draw a line
            line((0, 200), (0, 800))
            # translate the canvase
            translate(300, 0)
            # set a line cap style
            lineCap("round")
            # draw a line
            line((0, 200), (0, 800))
        """
        self._requiresNewFirstPage = True
        self._addInstruction("lineCap", value)

    def lineDash(self, value: float | None, *values: float, offset: float = 0):
        """
        Set a line dash with any given amount of lenghts.
        Uneven lenghts will have a visible stroke, even lenghts will be invisible.

        optionally an `offset` can be given to set the offset of the first dash.

        .. downloadcode:: lineDash.py

            # set stroke color to black
            stroke(0)
            # set a strok width
            strokeWidth(50)
            # translate the canvas
            translate(150, 50)
            # set a line dash
            lineDash(2, 2)
            # draw a line
            line((0, 200), (0, 800))
            # translate the canvas
            translate(200, 0)
            # set a line dash
            lineDash(2, 10, 5, 5)
            # draw a line
            line((0, 200), (0, 800))
            # translate the canvas
            translate(200, 0)
            # set a line dash and offset
            lineDash(2, 10, 5, 5, offset=2)
            # draw a line
            line((0, 200), (0, 800))
            # translate the canvase
            translate(200, 0)
            # reset the line dash
            lineDash(None)
            # draw a line
            line((0, 200), (0, 800))
        """
        combinedDashes: list[float | None] = [value]
        if isinstance(value, float | int):
            combinedDashes.extend(values)
        self._requiresNewFirstPage = True
        self._addInstruction("lineDash", combinedDashes, offset)

    # transform

    def transform(self, matrix: TransformTuple, center: Point = (0, 0)):
        """
        Transform the canvas with a transformation matrix.
        """
        self._requiresNewFirstPage = True
        if center != (0, 0):
            matrix = transformationAtCenter(matrix, center)
        self._addInstruction("transform", matrix)

    def translate(self, x: float = 0, y: float = 0):
        """
        Translate the canvas with a given offset.
        """
        self.transform((1, 0, 0, 1, x, y))

    def rotate(self, angle: float, center: Point = (0, 0)):
        """
        Rotate the canvas around the `center` point (which is the origin by default) with a given angle in degrees.
        """
        angle = math.radians(angle)
        c = math.cos(angle)
        s = math.sin(angle)
        self.transform((c, s, -s, c, 0, 0), center)

    def scale(self, x: float = 1, y: float | None = None, center: Point = (0, 0)):
        """
        Scale the canvas with a given `x` (horizontal scale) and `y` (vertical scale).

        If only 1 argument is provided a proportional scale is applied.

        The center of scaling can optionally be set via the `center` keyword argument. By default this is the origin.
        """
        if y is None:
            y = x
        self.transform((x, 0, 0, y, 0, 0), center)

    def skew(self, angle1: float, angle2: float = 0, center: Point = (0, 0)):
        """
        Skew the canvas with given `angle1` and `angle2`.

        If only one argument is provided a proportional skew is applied.

        The center of skewing can optionally be set via the `center` keyword argument. By default this is the origin.
        """
        angle1 = math.radians(angle1)
        angle2 = math.radians(angle2)
        self.transform((1, math.tan(angle2), math.tan(angle1), 1, 0, 0), center)

    # text

    def font(self, fontNameOrPath: SomePath, fontSize: float | None = None, fontNumber: int = 0):
        """
        Set a font with the name of the font.
        If a font path is given the font will be installed and used directly.
        Optionally a `fontSize` can be set directly.
        The default font, also used as fallback font, is 'LucidaGrande'.
        The default `fontSize` is 10pt.

        The name of the font relates to the font's postscript name.

        The font name is returned, which is handy when the font was loaded
        from a path.

        ::

            font("Times-Italic")
        """
        font = getNSFontFromNameOrPath(fontNameOrPath, fontSize or 10, fontNumber)
        self._dummyContext.font(fontNameOrPath, fontSize, fontNumber)
        self._addInstruction("font", fontNameOrPath, fontSize, fontNumber)
        return getFontName(font)

    def fallbackFont(self, fontNameOrPath: SomePath, fontNumber: int = 0):
        """
        Set a fallback font, this is used whenever a glyph is not available in the current font.

        ::

            fallbackFont("Times")
        """
        dummyFont = getNSFontFromNameOrPath(fontNameOrPath, 10, fontNumber)
        if dummyFont is None:
            raise DrawBotError("Fallback font '%s' is not available" % fontNameOrPath)
        self._dummyContext.fallbackFont(fontNameOrPath)
        self._addInstruction("fallbackFont", fontNameOrPath, fontNumber)
        return getFontName(dummyFont)

    def fontSize(self, fontSize: float):
        """
        Set the font size in points.
        The default `fontSize` is 10pt.

        ::

            fontSize(30)
        """
        self._dummyContext.fontSize(fontSize)
        self._addInstruction("fontSize", fontSize)

    def lineHeight(self, value):
        """
        Set the line height.

        .. downloadcode:: lineHeight.py

            # set line height
            lineHeight(150)
            # set font size
            fontSize(60)
            # draw text in a box
            textBox("Hello World " * 10, (100, 100, 800, 800))
        """
        self._dummyContext.lineHeight(value)
        self._addInstruction("lineHeight", value)

    def tracking(self, value: float):
        """
        Set the tracking between characters. It adds an absolute number of
        points between the characters.

        .. downloadcode:: tracking.py

            size(1000, 350)
            # set tracking
            tracking(100)
            # set font size
            fontSize(100)
            # draw some text
            text("hello", (100, 200))
            # disable tracking
            tracking(None)
            # draw some text
            text("world", (100, 100))
        """
        self._dummyContext.tracking(value)
        self._addInstruction("tracking", value)

    def baselineShift(self, value):
        """
        Set the shift of the baseline.
        """
        self._dummyContext.baselineShift(value)
        self._addInstruction("baselineShift", value)

    def underline(self, value: str):
        """
        Set the underline value.
        Underline must be `single`, `thick`, `double` or `None`.

        .. downloadcode:: underline.py

            underline("single")
            fontSize(140)
            text("hello underline", (50, 50))
        """
        self._dummyContext.underline(value)
        self._addInstruction("underline", value)

    def strikethrough(self, value: str):
        """
        Set the strikethrough value.
        Underline must be `single`, `thick`, `double` or `None`.

        .. downloadcode:: strikethrough.py

            size(1000, 200)
            strikethrough("single")
            fontSize(100)
            text("hello strikethrough", (40, 60))
        """
        self._dummyContext.strikethrough(value)
        self._addInstruction("strikethrough", value)

    def url(self, value: str):
        """
        Set the url value for text.

        .. downloadcode:: url.py

            fontSize(140)
            url("http://drawbot.com")
            text("hello", (100, 100))
        """
        self._dummyContext.url(value)
        self._addInstruction("url", value)

    def hyphenation(self, value: bool):
        """
        Set hyphenation, `True` or `False`.

        .. downloadcode:: hyphenation.py

            txt = '''Lorem ipsum dolor sit amet, consectetuer adipiscing elit, sed diam nonummy nibh euismod tincidunt ut laoreet dolore magna aliquam erat volutpat. Ut wisi enim ad minim veniam, quis nostrud exerci tation ullamcorper suscipit lobortis nisl ut aliquip ex ea commodo consequat. Duis autem vel eum iriure dolor in hendrerit in vulputate velit esse molestie consequat, vel illum dolore eu feugiat nulla facilisis at vero eros et accumsan et iusto odio dignissim qui blandit praesent luptatum zzril delenit augue duis dolore te feugait nulla facilisi. Nam liber tempor cum soluta nobis eleifend option congue nihil imperdiet doming id quod mazim placerat facer possim assum. Typi non habent claritatem insitam; est usus legentis in iis qui facit eorum claritatem. Investigationes demonstraverunt lectores legere me lius quod ii legunt saepius. Claritas est etiam processus dynamicus, qui sequitur mutationem consuetudium lectorum. Mirum est notare quam littera gothica, quam nunc putamus parum claram, anteposuerit litterarum formas humanitatis per seacula quarta decima et quinta decima. Eodem modo typi, qui nunc nobis videntur parum clari, fiant sollemnes in futurum.'''
            # enable hyphenation
            hyphenation(True)
            # set font size
            fontSize(50)
            # draw text in a box
            textBox(txt, (100, 100, 800, 800))
        """
        self._dummyContext.hyphenation(value)
        self._checkLanguageHyphenation()
        self._addInstruction("hyphenation", value)

    def tabs(self, *tabs: tuple[float, str]):
        r"""
        Set tabs, tuples of (`float`, `alignment`)
        Aligment can be `"left"`, `"center"`, `"right"` or any other character.
        If a character is provided the alignment will be `right` and centered on the specified character.

        .. downloadcode:: tabs.py

            t = " hello w o r l d"
            # replace all spaces by tabs
            t = t.replace(" ", "\t")
            # set some tabs
            tabs((85, "center"), (232, "right"), (300, "left"))
            # draw the string
            text(t, (10, 10))
            # reset all tabs
            tabs(None)
            # draw the same string
            text(t, (10, 50))
        """
        if tabs and tabs[0] is None:
            self._dummyContext.tabs(None)
            self._addInstruction("tabs", None)
            return
        self._dummyContext.tabs(*tabs)
        self._addInstruction("tabs", *tabs)

    def language(self, language):
        """
        Set the preferred language as language tag or None to use the default language.
        A language tag might be a [iso639-2 or iso639-1](https://www.loc.gov/standards/iso639-2/php/English_list.php)
        code or a locale identifier supported by local OS.
        A warning will be issued if the language tag is not supported.

        `language()` will activate the `locl` OpenType features, if supported by the current font.

        .. downloadcode:: language.py

            size(1000, 600)
            # a long dutch word
            word = "paardenkop"
            # a box where we draw in
            box = (100, 50, 400, 500)
            # set font size
            fontSize(118)
            # enable hyphenation
            hyphenation(True)
            # draw the text with no language set
            textBox(word, box)
            # set language to dutch (nl)
            language("nl")
            # shift up a bit
            translate(500, 0)
            # darw the text again with a language set
            textBox(word, box)
        """
        self._dummyContext.language(language)
        self._checkLanguageHyphenation()
        self._addInstruction("language", language)

    def listLanguages(self) -> dict[str, str]:
        """
        List all available languages as dictionary mapped to a readable language/dialect name.
        """
        loc = AppKit.NSLocale.currentLocale()
        return {
            tag: loc.displayNameForKey_value_(AppKit.NSLocaleIdentifier, tag)
            for tag in AppKit.NSLocale.availableLocaleIdentifiers()
        }

    def _checkLanguageHyphenation(self):
        language = self._dummyContext._state.text._language
        if language and self._dummyContext._state.hyphenation:
            locale = CoreText.CFLocaleCreate(None, language)
            if not CoreText.CFStringIsHyphenationAvailableForLocale(locale):
                warnings.warn(f"Language '{language}' has no hyphenation available.")

    def writingDirection(self, direction: str | None):
        """
        Set the writing direction: `None`, `'LTR'` or `'RTL'`.

        Use this when mixing writing directions.

        .. downloadcode:: textRTL.py

            size(400, 100)
            # A bi-directional string
            s = "Latin میتوان در بسیاری"
            # Set the writing direction to Right-To-Left
            writingDirection("RTL")
            fontSize(40)
            text(s, (10, 40))
        """
        self._dummyContext.writingDirection(direction)
        self._addInstruction("writingDirection", direction)

    def openTypeFeatures(self, *args: bool | None, **features: bool) -> dict[str, bool]:
        """
        Enable OpenType features.

        Supported OpenType tags:

        ::

            c2pc, c2sc, calt, case, cpsp, cswh, dlig, frac, liga, kern, lnum, onum, ordn, pnum, rlig, sinf, smcp, ss01, ss02, ss03, ss04, ss05, ss06, ss07, ss08, ss09, ss10, ss11, ss12, ss13, ss14, ss15, ss16, ss17, ss18, ss19, ss20, subs, sups, swsh, titl, tnum

        A `resetFeatures` argument can be set to `True` in order to get back to the default state.

        .. downloadcode:: openTypeFeatures.py

            newPage(1000, 300)
            # set a font
            font("Didot")
            # set the font size
            fontSize(50)
            # create a string
            someTxt = "aabcde1234567890"
            # draw the string
            text(someTxt, (100, 220))
            # enable some OpenType features
            openTypeFeatures(onum=True, smcp=True)
            # draw the same string
            text(someTxt, (100, 150))
            # reset defaults
            openTypeFeatures(resetFeatures=True)
            # the same string again, back to default features
            text(someTxt, (100, 70))
        """
        result = self._dummyContext.openTypeFeatures(*args, **features)
        self._addInstruction("openTypeFeatures", *args, **features)
        return result

    def listOpenTypeFeatures(self, fontNameOrPath: SomePath | None = None) -> list[str]:
        return self._dummyContext._state.text.listOpenTypeFeatures(fontNameOrPath)

    listOpenTypeFeatures.__doc__ = FormattedString.listOpenTypeFeatures.__doc__

    def fontVariations(self, *args: None, **axes: float | bool):
        """
        Pick a variation by axes values.

        .. downloadcode:: fontVariations.py

            size(1000, 500)
            # pick a font
            font("Skia")
            # pick a font size
            fontSize(200)
            # list all axis from the current font
            for axis, data in listFontVariations().items():
                print((axis, data))
            # pick a variation from the current font
            fontVariations(wght=.6)
            # draw text!!
            text("Hello Q", (100, 100))
            # pick a variation from the current font
            fontVariations(wght=3, wdth=1.2)
            # draw text!!
            text("Hello Q", (100, 300))
        """
        result = self._dummyContext.fontVariations(*args, **axes)
        self._addInstruction("fontVariations", *args, **axes)
        return result

    def listFontVariations(self, fontNameOrPath: SomePath | None = None) -> dict[str, dict]:
        return self._dummyContext._state.text.listFontVariations(fontNameOrPath)

    listFontVariations.__doc__ = FormattedString.listFontVariations.__doc__

    def fontNamedInstance(self, name: str, fontNameOrPath: SomePath | None = None):
        """
        Set a font with `name` of a named instance.
        The `name` of the named instance must be listed in `listNamedInstances()`,

        Optionally a `fontNameOrPath` can be given. If a font path is given that `fontNameOrPath` will be set.

        .. downloadcode:: fontNamedInstance.py

            newPage(500, 250)
            # pick font
            font("Skia", 200)
            # select a named instance
            fontNamedInstance("Skia-Regular_Black-Extended")
            # draw text!!
            text("abc", (50, 50))
        """
        self._dummyContext._state.text.fontNamedInstance(name, fontNameOrPath)
        self._addInstruction("fontNamedInstance", name, fontNameOrPath)

    def listNamedInstances(self, fontNameOrPath=None) -> dict[str, dict]:
        return self._dummyContext._state.text.listNamedInstances(fontNameOrPath)

    listNamedInstances.__doc__ = FormattedString.listNamedInstances.__doc__

    def textProperties(self) -> dict[str, Any]:
        return self._dummyContext._state.text.textProperties()

    textProperties.__doc__ = FormattedString.textProperties.__doc__

    # drawing text

    def text(self, txt: FormattedString | str, position: Point, align: str | None = None):
        """
        Draw a text at a provided position.

        Optionally an alignment can be set.
        Possible `align` values are: `"left"`, `"center"` and `"right"`.

        The default alignment is `left`.

        Optionally `txt` can be a `FormattedString`.

        .. downloadcode:: text.py

            # set a font and font size
            font("Times-Italic", 200)
            # draw text
            text("hallo", (200, 600))
            text("I'm Times", (100, 300))
        """
        if not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)
        x, y = position
        if align not in ("left", "center", "right", None):
            raise DrawBotError("align must be left, right, center")
        attributedString = self._dummyContext.attributedString(txt, align=align)
        for subTxt, box in makeTextBoxes(
            attributedString, (x, y), align=align, plainText=not isinstance(txt, FormattedString)
        ):
            if isinstance(txt, FormattedString):
                subTxt.copyContextProperties(txt)
            self.textBox(subTxt, box, align=align)

    def textOverflow(self, txt: FormattedString | str, box: BoundingBox, align: str | None = None):
        """
        Returns the overflowed text without drawing the text.

        A `box` could be a `(x, y, w, h)` or a bezierPath object.

        Optionally an alignment can be set.
        Possible `align` values are: `"left"`, `"center"`, `"right"` and `"justified"`.

        The default alignment is `left`.

        Optionally `txt` can be a `FormattedString`.
        Optionally `box` can be a `BezierPath`.
        """
        if isinstance(txt, FormattedString):
            txt = txt.copy()
        elif not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)
        if align is None:
            align = "left"
        elif align not in self._dummyContext._textAlignMap.keys():
            raise DrawBotError("align must be %s" % (", ".join(self._dummyContext._textAlignMap.keys())))
        return self._dummyContext.clippedText(txt, box, align)

    def textBox(self, txt: FormattedString | str, box: BoundingBox, align: str | None = None):
        """
        Draw a text in a provided rectangle.

        A `box` could be a `(x, y, w, h)` or a bezierPath object.

        Optionally an alignment can be set.
        Possible `align` values are: `"left"`, `"center"`, `"right"` and `"justified"`.

        If the text overflows the rectangle, the overflowed text is returned.

        The default alignment is `left`.

        .. downloadcode:: textBox.py

            # a box has an x, y, width and height
            x, y, w, h = 100, 100, 800, 800
            # set a fill
            fill(1, 0, 0)
            # draw a rectangle with variables from above
            rect(x, y, w, h)
            # set a diferent fill
            fill(1)
            # set a font size
            fontSize(200)
            # draw text in a text box
            # with varibales from above
            overflow = textBox("hallo, this text is a bit to long",
                            (x, y, w, h), align="center")
            # a text box returns text overflow
            # text that did not make it into the box
            print(overflow)

        The returned overflow can be used to add new pages until all text is set:

        .. downloadcode:: overflowText.py

            t = '''DrawBot is a powerful, free application for MacOSX that invites you to write simple Python scripts to generate two-dimensional graphics. The builtin graphics primitives support rectangles, ovals, (bezier) paths, polygons, text objects and transparency.
            DrawBot is an ideal tool to teach the basics of programming. Students get colorful graphic treats while getting familiar with variables, conditional statements, functions and what have you. Results can be saved in a selection of different file formats, including as high resolution, scaleable PDF.
            DrawBot has proven itself as part of the curriculum at selected courses at the Royal Academy in The Hague.'''

            # setting some variables
            # setting the size
            x, y, w, h = 10, 10, 480, 480

            # setting the color change over different frames
            coloradd = .1

            # setting the start background color only red and blue
            r = .3
            b = 1

            # start a loop and run as long there is t variable has some text
            while len(t):
                # create a new page
                newPage(500, 500)
                # set a frame duration
                frameDuration(3)
                # set the background fill
                fill(r, 0, b)
                # draw the background
                rect(x, y, w, h)
                # set a fill color
                fill(0)
                # set a font with a size
                font("DrawBot-Bold", randint(50, 100))
                # pick some random colors
                rr = random()
                gg = random()
                bb = random()
                # set a gradient as fill
                radialGradient((250, 250), (250, 250), [(rr, gg, bb), (1-rr, 1-gg, 1-bb)], startRadius=0, endRadius=250)

                # draw the text in a box with the gradient fill
                t = textBox(t, (x, y, w, h))

                # setting the color for the next frame
                r += coloradd
                b -= coloradd

                # set a font
                font("DrawBot-Bold", 20)
                # get the page count text size as a (width, height) tuple
                tw, th = textSize("%s" % pageCount())
                # draw the text
                textBox("%s" % pageCount(), (10, 10, 480, th), align="center")

            saveImage("~/Desktop/drawbot.mp4")

        Another example, this time using a bezierPath as a text envelope:

        .. downloadcode:: textBoxInPath.py

            # create a fresh bezier path
            path = BezierPath()
            # draw some text
            # the text will be converted to curves
            path.text("a", font="Helvetica-Bold", fontSize=500)
            # set an indent
            indent = 50
            # calculate the width and height of the path
            minx, miny, maxx, maxy = path.bounds()
            w = maxx - minx
            h = maxy - miny
            # calculate the box where we want to draw the path in
            boxWidth = width() - indent * 2
            boxHeight = height() - indent * 2
            # calculate a scale based on the given path bounds and the box
            s = min([boxWidth / float(w), boxHeight / float(h)])
            # translate to the middle
            translate(width()*.5, height()*.5)
            # set the scale
            scale(s)
            # translate the negative offset, letter could have overshoot
            translate(-minx, -miny)
            # translate with half of the width and height of the path
            translate(-w*.5, -h*.5)
            # draw the path
            drawPath(path)
            # set a font
            font("Helvetica-Light")
            # set a font size
            fontSize(5)
            # set white as color
            fill(1)
            # draw some text in the path
            textBox("abcdefghijklmnopqrstuvwxyz"*30000, path)
        """
        if not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)
        if align is None:
            align = "left"
        elif align not in self._dummyContext._textAlignMap.keys():
            raise DrawBotError("align must be %s" % (", ".join(self._dummyContext._textAlignMap.keys())))
        self._requiresNewFirstPage = True
        self._addInstruction("textBox", txt, box, align)
        return self._dummyContext.clippedText(txt, box, align)

    def textBoxBaselines(self, txt: FormattedString | str, box: BoundingBox, align: str | None = None):
        """
        Returns a list of `x, y` coordinates
        indicating the start of each line
        for a given `text` in a given `box`.

        A `box` could be a `(x, y, w, h)` or a bezierPath object.

        Optionally an alignment can be set.
        Possible `align` values are: `"left"`, `"center"`, `"right"` and `"justified"`.
        """
        if not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)
        path, (x, y) = self._dummyContext._getPathForFrameSetter(box)
        attrString = self._dummyContext.attributedString(txt, align=align)
        setter = newFramesetterWithAttributedString(attrString)
        box = CoreText.CTFramesetterCreateFrame(setter, (0, 0), path, None)
        ctLines = CoreText.CTFrameGetLines(box)
        origins = CoreText.CTFrameGetLineOrigins(box, (0, len(ctLines)), None)
        return [(x + o.x, y + o.y) for o in origins]

    def textBoxCharacterBounds(self, txt: FormattedString | str, box: BoundingBox, align: str | None = None):
        """
        Returns a list of typesetted bounding boxes `((x, y, w, h), baseLineOffset, formattedSubString)`.

        A `box` could be a `(x, y, w, h)` or a bezierPath object.

        Optionally an alignment can be set.
        Possible `align` values are: `"left"`, `"center"`, `"right"` and `"justified"`.
        """
        if not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)

        CharactersBounds = namedtuple("CharactersBounds", ["bounds", "baselineOffset", "formattedSubString"])

        bounds = list()
        path, (x, y) = self._dummyContext._getPathForFrameSetter(box)
        attrString = self._dummyContext.attributedString(txt)
        setter = newFramesetterWithAttributedString(attrString)
        box = CoreText.CTFramesetterCreateFrame(setter, (0, 0), path, None)
        ctLines = CoreText.CTFrameGetLines(box)
        origins = CoreText.CTFrameGetLineOrigins(box, (0, len(ctLines)), None)
        for i, (originX, originY) in enumerate(origins):
            ctLine = ctLines[i]
            ctRuns = CoreText.CTLineGetGlyphRuns(ctLine)
            for ctRun in ctRuns:
                runRange = CoreText.CTRunGetStringRange(ctRun)
                runPos = CoreText.CTRunGetPositions(ctRun, (0, 1), None)[0]
                runW, runH, ascent, descent = CoreText.CTRunGetTypographicBounds(ctRun, (0, 0), None, None, None)
                bounds.append(
                    CharactersBounds(
                        (x + originX + runPos.x, y + originY + runPos.y - ascent, runW, runH + ascent),
                        ascent,
                        txt[runRange.location : runRange.location + runRange.length],
                    )
                )
        return bounds

    # images
    def image(
        self,
        path: SomePath | ImageObject,  # FIXME path as argument name might be misleading
        position: Point,
        alpha: float = 1,
        pageNumber: int | None = None,
    ):
        """
        Add an image from a `path` with an `offset` and an `alpha` value.
        This accepts most common file types like pdf, jpg, png, tiff and gif. `NSImage` objects are accepted too.

        Optionally an `alpha` can be provided, which is a value between 0 and 1.

        Optionally a `pageNumber` can be provided when the path referes to a multi page pdf file.

        .. downloadcode:: image.py

            # the path can be a path to a file or a url, image from https://www.wired.com/2012/08/a1-art-0-99-vending-machines-as-art-galleries/
            image("https://raw.githubusercontent.com/typemytype/drawbot/master/tests/data/drawBot.jpg", (100, 100), alpha=.3)
        """
        if isinstance(path, ImageObject):
            path = path._nsImage()
        if isinstance(path, (str, os.PathLike)):
            path = optimizePath(path)
        self._requiresNewFirstPage = True
        self._addInstruction("image", path, position, alpha, pageNumber)

    def imageSize(
        self,
        path: SomePath | ImageObject,  # FIXME path as argument name might be misleading
        pageNumber: int | None = None,
    ) -> tuple[float, float]:
        """
        Return the `width` and `height` of an image. Supports pdf, jpg, png, tiff and gif file formats. `NSImage` objects are supported too.

        .. downloadcode:: imageSize.py

            print(imageSize("https://raw.githubusercontent.com/typemytype/drawbot/master/tests/data/drawBot.jpg"))
        """
        if isinstance(path, ImageObject):
            # its an drawBot.ImageObject, just return the size from that obj
            return path.size()

        _hasPixels = False

        if isinstance(path, AppKit.NSImage):
            # it is an NSImage
            reps = path.representations()
            if not reps:
                # raise error when no representation are found
                raise DrawBotError("Cannot extract bitmap data from given nsImage object")
            # get the bitmap representation
            _hasPixels = True
            rep = reps[0]
        else:
            if isinstance(path, (str, os.PathLike)):
                path = optimizePath(path)
            if isinstance(path, str) and path.startswith("http"):
                url = AppKit.NSURL.URLWithString_(path)
            else:
                if isinstance(path, str) and not os.path.exists(path):
                    raise DrawBotError("Image does not exist")
                url = AppKit.NSURL.fileURLWithPath_(path)
            # check if the file is an .pdf
            _isPDF, pdfDocument = isPDF(url)
            # check if the file is an .eps
            _isEPS, epsRep = isEPS(url)
            # check if the file is an .gif
            _isGIF, gifRep = isGIF(url)
            if _isEPS:
                rep = epsRep
            elif _isPDF and pageNumber is None:
                rep = AppKit.NSImage.alloc().initByReferencingURL_(url)
            elif _isGIF and pageNumber is not None:
                rep = gifTools.gifFrameAtIndex(url, pageNumber - 1)
            elif _isPDF and pageNumber is not None:
                page = pdfDocument.pageAtIndex_(pageNumber - 1)
                mediaBox = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
                return mediaBox.size.width, mediaBox.size.height
            else:
                _hasPixels = True
                rep = AppKit.NSImageRep.imageRepWithContentsOfURL_(url)

        if _hasPixels:
            w, h = rep.pixelsWide(), rep.pixelsHigh()
        else:
            w, h = rep.size()
        return w, h

    def imagePixelColor(
        self,
        path: SomePath | ImageObject,  # FIXME path as argument name might be misleading
        xy: Point,
    ) -> RGBAColorTuple | None:
        """
        Return the color `r, g, b, a` of an image at a specified `x`, `y` position.
        Supports pdf, jpg, png, tiff and gif file formats. `NSImage` objects are supported too.

        .. downloadcode:: pixelColor.py

            # path to the image, from https://www.wired.com/2012/08/a1-art-0-99-vending-machines-as-art-galleries/
            path = "https://raw.githubusercontent.com/typemytype/drawbot/master/tests/data/drawBot.jpg"

            # get the size of the image
            w, h = imageSize(path)

            # setup a variable for the font size as for the steps
            s = 15

            # shift it up a bit
            translate(100, 100)

            # set a font with a size
            font("Helvetica-Bold")
            fontSize(s)

            # loop over the width of the image
            for x in range(0, w, s):
                # loop of the height of the image
                for y in range(0, h, s):
                    # get the color
                    color = imagePixelColor(path, (x, y))
                    if color:
                        r, g, b, a = color
                        # set the color
                        fill(r, g, b, a)
                        # draw some text
                        text("W", (x, y))
        """
        x, y = xy
        if isinstance(path, (str, os.PathLike)):
            path = optimizePath(path)
        bitmap = self._cachedPixelColorBitmaps.get(path)
        if bitmap is None:
            if isinstance(path, ImageObject):
                source = path._nsImage()
            elif isinstance(path, AppKit.NSImage):
                source = path
            else:
                if isinstance(path, str) and path.startswith("http"):
                    url = AppKit.NSURL.URLWithString_(path)
                else:
                    url = AppKit.NSURL.fileURLWithPath_(path)
                source = AppKit.NSImage.alloc().initByReferencingURL_(url)

            bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(source.TIFFRepresentation())
            self._cachedPixelColorBitmaps[path] = bitmap

        color = bitmap.colorAtX_y_(x, bitmap.pixelsHigh() - y - 1)
        if color is None:
            return None
        color = color.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
        return color.redComponent(), color.greenComponent(), color.blueComponent(), color.alphaComponent()

    def imageResolution(self, path: SomePath | AppKit.NSImage) -> int:
        """
        Return the image resolution for a given image. Supports pdf, jpg, png, tiff and gif file formats. `NSImage` objects are supported too.
        """
        if isinstance(path, AppKit.NSImage):
            # its an NSImage
            # get all representations
            reps = path.representations()
            if not reps:
                # raise error when no representation are found
                raise DrawBotError("Cannot extract bitmap data from given nsImage object")
            # get the bitmap representation
            rep = reps[0]
        else:
            if isinstance(path, (str, os.PathLike)):
                path = optimizePath(path)
            if path.startswith("http"):
                url = AppKit.NSURL.URLWithString_(path)
            else:
                if not os.path.exists(path):
                    raise DrawBotError("Image does not exist")
                url = AppKit.NSURL.fileURLWithPath_(path)
                try:
                    rep = AppKit.NSImageRep.imageRepWithContentsOfURL_(url)
                except Exception:
                    raise DrawBotError("Cannot read bitmap data for image '%s'" % path)

        return rep.pixelsWide() / rep.size().width * 72.0

    def numberOfPages(self, path: SomePath) -> int | None:
        """
        Return the number of pages for a given pdf or (animated) gif.
        Return `None` for non compatible file types.

        """
        path = optimizePath(path)
        if isinstance(path, str) and path.startswith("http"):
            url = AppKit.NSURL.URLWithString_(path)
        else:
            url = AppKit.NSURL.fileURLWithPath_(path)
        pdf = Quartz.CGPDFDocumentCreateWithURL(url)
        if pdf:
            return Quartz.CGPDFDocumentGetNumberOfPages(pdf)
        _isGIF, _ = isGIF(url)
        if _isGIF:
            frameCount = gifTools.gifFrameCount(url)
            if frameCount:
                return frameCount
        return None

    # mov

    def frameDuration(self, seconds: float):
        """
        When exporting to `mov` or `gif` each frame can have duration set in `seconds`.

        .. downloadcode:: frameDuration.py

            # setting some variables
            # size of the pages / frames
            w, h = 200, 200
            # frame per seconds
            fps = 30
            # duration of the movie
            seconds = 3
            # calculate the lenght of a single frame
            duration = 1 / fps
            # calculate the amount of frames needed
            totalFrames = seconds * fps

            # title page
            newPage(w, h)
            # set frame duration to 1 second
            frameDuration(1)
            # pick a font and font size
            font("Helvetica", 40)
            # draw the title text in a box
            textBox("Rotated square", (0, 0, w, h * .8), align="center")

            # loop over the amount of frames needed
            for i in range(totalFrames):
                # create a new page
                newPage(w, h)
                # set the frame duration
                frameDuration(duration)
                # set a fill color
                fill(1, 0, 0)
                # translate to the center of the page
                translate(w / 2, h / 2)
                # rotate around the center
                rotate(i*10)
                # draw the rect
                rect(-50, -50, 50, 50)

            # save the image as a mov on the desktop
            saveImage('~/Desktop/frameDuration.gif')
        """
        self._requiresNewFirstPage = True
        self._addInstruction("frameDuration", seconds)

    # pdf links

    def linkURL(self, url: str, xywh: BoundingBox):
        """
        Add a clickable rectangle for an external url link.

        The link rectangle will be set independent of the current context transformations.
        """
        x, y, w, h = xywh
        self._requiresNewFirstPage = True
        self._addInstruction("linkURL", url, (x, y, w, h))

    def linkDestination(self, name: str, xy: Point):
        """
        Add a destination point for a link within a PDF.
        Setup a clickable retangle with `linkRect(name, (x, y, w, h))` with the same name.

        The destination position will be set independent of the current context transformations.
        """
        x, y = xy
        self._requiresNewFirstPage = True
        self._addInstruction("linkDestination", name, (x, y))

    def linkRect(self, name: str, xywh: BoundingBox):
        """
        Add a clickable rectangle for a link within a PDF.
        Use `linkDestination(name, (x, y))` with the same name to set the destination of the clickable rectangle.

        The link rectangle will be set independent of the current context transformations.

        .. downloadcode:: linkRect.py

            # a variable with the amount of pages we want
            totalPages = 10
            # create the first page with a index
            newPage()
            # set a font size
            fontSize(30)
            # start a loop over all wanted pages
            for i in range(totalPages):
                # set a random fill color
                fill(i/(totalPages-1), .5, i/(totalPages-1))
                # draw a rectangle
                rect(10, 50 * i, 50, 50)
                fill(1)
                textBox(f"{i}", (10, 50 * i, 50, 50))
                # add a clickable link rectangle with a unique name
                linkRect(f"beginPage_{i}", (10, 10 + 50 * i, 50, 50))

            # start a loop over all wanted pages
            for i in range(totalPages):
                # create a new page
                newPage()
                fontSize(200)
                text(f"Page {i}", (30, 30))
                # add a link destination with a given name
                # the name must refer to a linkRect name
                oval(width()/2-10, height()/2-10, 20, 20)
                linkDestination(f"beginPage_{i}", (width()/2, height()/2))

        """
        x, y, w, h = xywh
        self._requiresNewFirstPage = True
        self._addInstruction("linkRect", name, (x, y, w, h))

    # helpers

    def textSize(
        self,
        txt: FormattedString | str,
        align: str | None = None,
        width: float | None = None,
        height: float | None = None,
    ):
        """
        Returns the size of a text with the current settings,
        like `font`, `fontSize` and `lineHeight` as a tuple (width, height).

        Optionally a `width` constrain or `height` constrain can be provided
        to calculate the lenght or width of text with the given constrain.
        """
        if not isinstance(txt, (str, FormattedString)):
            raise TypeError("expected 'str' or 'FormattedString', got '%s'" % type(txt).__name__)
        if width is not None and height is not None:
            raise DrawBotError("Calculating textSize can only have one constrain, either width or height must be None")
        return self._dummyContext.textSize(txt, align, width, height)

    def installedFonts(self, supportsCharacters: str | None = None) -> list[str]:
        """
        Returns a list of all installed fonts.

        Optionally a string with `supportsCharacters` can be provided,
        the list of available installed fonts will be filtered by
        support of these characters,
        """
        if supportsCharacters is not None:
            if len(supportsCharacters) == 0:
                raise DrawBotError("supportsCharacters must contain at least one character")
            characterSet = AppKit.NSCharacterSet.characterSetWithCharactersInString_(supportsCharacters)
            fontAttributes = {CoreText.NSFontCharacterSetAttribute: characterSet}
            fontDescriptor = CoreText.CTFontDescriptorCreateWithAttributes(fontAttributes)
            descriptions = fontDescriptor.matchingFontDescriptorsWithMandatoryKeys_(None)
            if descriptions is not None:
                return [str(description[CoreText.NSFontNameAttribute]) for description in descriptions]
            else:
                return []  # No font was found that supports the requested characters
        return [str(f) for f in AppKit.NSFontManager.sharedFontManager().availableFonts()]

    def installFont(self, path: SomePath) -> str:
        """
        Install a font with a given path and the postscript font name will be returned.
        The postscript font name can be used to set the font as the active font.

        Fonts are installed only for the current process.
        Fonts will not be accesible outside the scope of drawBot.

        All installed fonts will automatically be uninstalled when the script is done.

        .. downloadcode:: installFont.py

            # set the path to a font file
            path = "path/to/font/file.otf"
            # install the font
            fontName = installFont(path)
            # set the font
            font(fontName, 200)
            # draw some text
            text("Hello World", (10, 10))
            # uninstall font
            uninstallFont(path)

        This function has been deprecated: please use the font path directly in all
        places that accept a font name.
        """
        warnings.warn(
            "installFont(path) has been deprecated, use the font path directly in all places that accept a font name."
        )
        path = os.fspath(path)
        if path in self._tempInstalledFonts:
            return self._tempInstalledFonts[path]

        success, error = self._dummyContext.installFont(path)
        self._addInstruction("installFont", path)

        psName = self._dummyContext._fontNameForPath(path)
        self._tempInstalledFonts[path] = psName
        # also clear cached memoized functions
        clearMemoizeCache()

        if not success:
            warnings.warn("install font: %s" % error)
        return psName

    def uninstallFont(self, path: SomePath):
        """
        Uninstall a font with a given path.

        This function has been deprecated: please use the font path directly in all
        places that accept a font name.
        """
        warnings.warn(
            "uninstallFont(path) has been deprecated, use the font path directly in all places that accept a font name."
        )
        path = os.fspath(path)
        success, error = self._dummyContext.uninstallFont(path)
        if path in self._tempInstalledFonts:
            del self._tempInstalledFonts[path]
        if not success:
            warnings.warn("uninstall font: %s" % error)
        self._addInstruction("uninstallFont", path)

    def _uninstallAllFonts(self):
        for path in self._tempInstalledFonts:
            self._dummyContext.uninstallFont(path)
        self._tempInstalledFonts = dict()

    def fontContainsCharacters(self, characters: str) -> bool:
        """
        Return a bool if the current font contains the provided `characters`.
        Characters is a string containing one or more characters.
        """
        return self._dummyContext._state.text.fontContainsCharacters(characters)

    def fontContainsGlyph(self, glyphName: str) -> bool:
        """
        Return a bool if the current font contains a provided glyph name.
        """
        return self._dummyContext._state.text.fontContainsGlyph(glyphName)

    def fontFilePath(self) -> SomePath:
        """
        Return the path to the file of the current font.
        """
        return self._dummyContext._state.text.fontFilePath()

    def fontFileFontNumber(self) -> int:
        """
        Return the font number (index) the current font it its container file.
        """
        return self._dummyContext._state.text.fontFileFontNumber()

    def listFontGlyphNames(self) -> list[str]:
        """
        Return a list of glyph names supported by the current font.
        """
        return self._dummyContext._state.text.listFontGlyphNames()

    def fontAscender(self) -> float:
        """
        Returns the current font ascender, based on the current `font` and `fontSize`.
        """
        return self._dummyContext._state.text.fontAscender()

    def fontDescender(self) -> float:
        """
        Returns the current font descender, based on the current `font` and `fontSize`.
        """
        return self._dummyContext._state.text.fontDescender()

    def fontXHeight(self) -> float:
        """
        Returns the current font x-height, based on the current `font` and `fontSize`.
        """
        return self._dummyContext._state.text.fontXHeight()

    def fontCapHeight(self) -> float:
        """
        Returns the current font cap height, based on the current `font` and `fontSize`.
        """
        return self._dummyContext._state.text.fontCapHeight()

    def fontLeading(self) -> float:
        """
        Returns the current font leading, based on the current `font` and `fontSize`.
        """
        return self._dummyContext._state.text.fontLeading()

    def fontLineHeight(self) -> float:
        """
        Returns the current line height, based on the current `font` and `fontSize`.
        If a `lineHeight` is set, this value will be returned.
        """
        return self._dummyContext._state.text.fontLineHeight()

    def Variable(self, variables, workSpace, continuous=True):
        """
        Build small UI for variables in a script.

        The `workSpace` is usually `globals()`
        as you want to insert the variable in the current workspace.
        It is required that `workSpace` is a `dict` object.

        The `continuous` argument controls whether the script is run when UI
        elements change. The default is `True`, which will execute the script
        immediately and continuously when the user input changes. When set to
        `False`, there will be an "Update" button added at the bottom of the window.
        The user will have to click this button to execute the script and see the
        changes. This is useful when the script is slow, and continuous execution
        would decrease responsiveness.

        .. image:: assets/variables.png

        .. downloadcode:: variablesUI.py

            # create small ui element for variables in the script

            Variable([
                # create a variable called 'w'
                # and the related ui is a Slider.
                dict(name="w", ui="Slider"),
                # create a variable called 'h'
                # and the related ui is a Slider.
                dict(name="h", ui="Slider",
                        args=dict(
                            # some vanilla specific
                            # setting for a slider
                            value=100,
                            minValue=50,
                            maxValue=300)),
                # create a variable called 'useColor'
                # and the related ui is a CheckBox.
                dict(name="useColor", ui="CheckBox"),
                # create a variable called 'c'
                # and the related ui is a ColorWell.
                dict(name="c", ui="ColorWell")
                ], globals())

            # draw a rect
            rect(0, 0, w, h)

            # check if the 'useColor' variable is checked
            if useColor:
                # set the fill color from the variables
                fill(c)
            # set the font size
            fontSize(h)
            # draw some text
            text("Hello Variable", (w, h))

        .. downloadcode:: vanillaVariablesUI.py

            # Variable == vanilla power in DrawBot
            from AppKit import NSColor
            # create a color
            _color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0, .5, 1, .8)
            # setup variables using different vanilla ui elements.
            Variable([
                dict(name="aList", ui="PopUpButton", args=dict(items=['a', 'b', 'c', 'd'])),
                dict(name="aText", ui="EditText", args=dict(text='hello world')),
                dict(name="aSlider", ui="Slider", args=dict(value=100, minValue=50, maxValue=300)),
                dict(name="aCheckBox", ui="CheckBox", args=dict(value=True)),
                dict(name="aColorWell", ui="ColorWell", args=dict(color=_color)),
                dict(name="aRadioGroup", ui="RadioGroup", args=dict(titles=['I', 'II', 'III'], isVertical=False)),
            ], globals())

            print(aList)
            print(aText)
            print(aSlider)
            print(aCheckBox)
            print(aColorWell)
            print(aRadioGroup)
        """
        document = AppKit.NSDocumentController.sharedDocumentController().currentDocument()
        if not document:
            raise DrawBotError("There is no document open")
        controller = document.vanillaWindowController
        try:
            controller._variableController.buildUI(variables, continuous=continuous)
            controller._variableController.show()
        except Exception:
            controller._variableController = VariableController(
                variables, controller.runCode, document, continuous=continuous
            )

        data = controller._variableController.get()
        for v, value in data.items():
            workSpace[v] = value


_drawBotDrawingTool = DrawBotDrawingTool()

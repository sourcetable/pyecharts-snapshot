import os
import sys
import base64
import codecs
import asyncio
from io import BytesIO

import pyecharts_snapshot.logger as logger
from PIL import Image
from pyppeteer import launch

DEFAULT_DELAY = 1.5
DEFAULT_PIXEL_RATIO = 2
PNG_FORMAT = "png"
JPG_FORMAT = "jpeg"
GIF_FORMAT = "gif"
PDF_FORMAT = "pdf"
SVG_FORMAT = "svg"
EPS_FORMAT = "eps"
B64_FORMAT = "base64"

SUPPORTED_IMAGE_FORMATS = [
    PNG_FORMAT,
    JPG_FORMAT,
    GIF_FORMAT,
    PDF_FORMAT,
    SVG_FORMAT,
    EPS_FORMAT,
    B64_FORMAT,
]

HELP_TEXT = """
Usage:   snapshot input file [%s] [delay] [pixel ratio] [output_json_file]
         snapshot help: display this help message
Parameters:
         delay: float value, unit in seconds and defaults 1.5 seconds
         pixel ratio: integer value, defaults to 2
         output_json_file: optional file path to save echarts options as JSON
         document online:github.com/pyecharts/pyecharts-snapshot
""".format(
    "|".join(SUPPORTED_IMAGE_FORMATS)
)
DEFAULT_OUTPUT_NAME = "output.%s"
NOT_SUPPORTED_FILE_TYPE = "Not supported file type '%s'"

MESSAGE_GENERATING = "Generating file ..."
MESSAGE_FILE_SAVED_AS = "File saved in %s"
SNAPSHOT_JS = """
async () => {
    const getEcharts = () => {
        var ele = document.querySelector('div[_echarts_instance_]');
        var mychart = echarts.getInstanceByDom(ele);
        // Width and height only works for off-screen canvas
        // Which this isn't. Therefore force a resize.
        chart.resize({
            width: %s,
            height: %s
        });
        return mychart.getDataURL({
            type: '%s',
            pixelRatio: %s,
            excludeComponents: ['toolbox']
        });
    }

    const delayedFunction = () => {
        return new Promise(function(resolve, reject){
            window.setTimeout(() => resolve(getEcharts()), %d);
        });
    }
    return await delayedFunction();
}
"""

SNAPSHOT_SVG_JS = """
async () => {
    const getEcharts = () => {
        var element = document.querySelector('div[_echarts_instance_] div');
        return element.innerHTML;
    }
    const delayedFunction = () => {
        return new Promise(function(resolve, reject){
            window.setTimeout(() => resolve(getEcharts()), %d);
        });
    }
    return await delayedFunction();
}
"""

CONFIG_JS = """
async () => {
    const getEchartsConfig = () => {
        var ele = document.querySelector('div[_echarts_instance_]');
        var mychart = echarts.getInstanceByDom(ele);
        return JSON.stringify(mychart.getOption());
    }
    const delayedFunction = () => {
        return new Promise(function(resolve, reject){
            window.setTimeout(() => resolve(getEchartsConfig()), %d);
        });
    }
    return await delayedFunction();
}
"""


def main():
    asyncio.get_event_loop().run_until_complete(_main())


async def _main():
    if len(sys.argv) < 2 or len(sys.argv) > 6:
        show_help()
    file_name = sys.argv[1]
    if file_name == "help":
        show_help()
    delay = DEFAULT_DELAY
    output = DEFAULT_OUTPUT_NAME % PNG_FORMAT
    pixel_ratio = DEFAULT_PIXEL_RATIO
    output_json_file = None

    if len(sys.argv) >= 3:
        file_type = sys.argv[2]
        if file_type in SUPPORTED_IMAGE_FORMATS:
            output = DEFAULT_OUTPUT_NAME % file_type
        elif file_type != PNG_FORMAT:
            raise TypeError(NOT_SUPPORTED_FILE_TYPE % file_type)
        if len(sys.argv) >= 4:
            delay = float(sys.argv[3])  # in seconds
            if len(sys.argv) >= 5:
                pixel_ratio = sys.argv[4]
                if len(sys.argv) == 6:
                    output_json_file = sys.argv[5]

    await make_a_snapshot(
        file_name, output, delay=delay, pixel_ratio=pixel_ratio,
        output_json_file=output_json_file
    )


def show_help():
    logger.info(HELP_TEXT)
    exit(0)


async def make_a_snapshot(
    file_name: str,
    output_name: str,
    delay: float = DEFAULT_DELAY,
    pixel_ratio: int = DEFAULT_PIXEL_RATIO,
    width: int | None = None,
    height: int | None = None,
    verbose: bool = True,
    output_json_file: str = None,
):
    logger.VERBOSE = verbose
    logger.info(MESSAGE_GENERATING)
    file_type = output_name.split(".")[-1]

    content, config = await async_make_snapshot(
        file_name, file_type, pixel_ratio, width, height, delay
    )

    if output_json_file:
        save_as_text(config, output_json_file)
        if "/" not in output_json_file:
            output_json_file = os.path.join(os.getcwd(), output_json_file)
        logger.info(MESSAGE_FILE_SAVED_AS % output_json_file)

    if file_type in [SVG_FORMAT, B64_FORMAT]:
        save_as_text(content, output_name)
    else:
        # pdf, gif, png, jpeg
        content_array = content.split(",")

        if len(content_array) != 2:
            raise OSError(content_array)
        base64_imagedata = content_array[1]
        imagedata = decode_base64(base64_imagedata)
        if file_type in [PDF_FORMAT, GIF_FORMAT, EPS_FORMAT]:
            save_as(imagedata, output_name, file_type)
        elif file_type in [PNG_FORMAT, JPG_FORMAT]:
            save_as_png(imagedata, output_name)
        else:
            pass
    if "/" not in output_name:
        output_name = os.path.join(os.getcwd(), output_name)

    logger.info(MESSAGE_FILE_SAVED_AS % output_name)


async def async_make_snapshot(
    html_path: str, file_type: str, pixel_ratio: int, width: int | None, height: int | None, delay: int
):
    __actual_delay_in_ms = int(delay * 1000)

    if file_type == "svg":
        snapshot_js = SNAPSHOT_SVG_JS % __actual_delay_in_ms
    else:
        snapshot_js = SNAPSHOT_JS % (
            width or '"auto"',
            height or '"auto"',
            file_type,
            pixel_ratio,
            __actual_delay_in_ms,
        )

    config_js = CONFIG_JS % __actual_delay_in_ms

    return await get_echarts(to_file_uri(html_path), snapshot_js, config_js)

async def get_echarts(url: str, snapshot_js: str, config_js: str = None):
    """Get both the snapshot and config in a single browser session"""
    args = os.environ.get('CHROME_EXTRA_ARGS', '')
    args = args.split(' ')

    browser = await launch(options=dict(
        headless=(os.getenv('PYPPETEER_HEADLESS', 'true').lower() in ('true', 'yes', '1')),
        executablePath=os.getenv('PYPPETEER_EXECUTABLE_PATH')
    ), args=args)

    page = await browser.newPage()
    await page.goto(url)

    # Get the snapshot first
    content = await page.evaluate(snapshot_js)

    # Then get the config
    config = await page.evaluate(config_js) if config_js else None

    await browser.close()

    return (content, config) if config else content


def decode_base64(data: str) -> bytes:
    """Decode base64, padding being optional.

    :param data: Base64 data as an ASCII byte string
    :returns: The decoded byte string.

    """
    missing_padding = len(data) % 4
    if missing_padding != 0:
        data += "=" * (4 - missing_padding)
    return base64.b64decode(data.encode("utf-8"))


def save_as_png(imagedata: bytes, output_name: str):
    with open(output_name, "wb") as f:
        f.write(imagedata)


def save_as_text(imagedata: str, output_name: str):
    with codecs.open(output_name, "w", encoding="utf-8") as f:
        f.write(imagedata)


def save_as(imagedata: bytes, output_name: str, file_type: str):
    m = Image.open(BytesIO(imagedata))
    m.load()
    color = (255, 255, 255)
    b = Image.new("RGB", m.size, color)
    b.paste(m, mask=m.split()[3])
    b.save(output_name, file_type, quality=100)


def to_file_uri(a_file_name: str) -> str:
    __universal_file_name = a_file_name.replace("\\", "/")
    if ":" not in a_file_name:
        __universal_file_name = os.path.abspath(__universal_file_name)
    return "file:///{0}".format(__universal_file_name)

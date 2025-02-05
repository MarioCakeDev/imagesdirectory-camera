import logging

# libraries for homeassistant setup service
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

# libraries need for custom code
from PIL import Image
import os
import shutil
import time
import datetime
import imageio
import numpy


DOMAIN = "imagedirectory"

_LOGGER = logging.getLogger(__name__)

from homeassistant.const import CONF_EXCLUDE

SERVICE_CREATE = "create_gif_mp4"
SERVICE_DEL = "delete_files"
SERVICE_MOVE = "move_files"

SERVICE_PARAM_SOURCE = "sourcepath"
SERVICE_PARAM_DELAY_TIME = "delay_time"
SERVICE_PARAM_MAX_FPS = "max_fps"
SERVICE_PARAM_DESTINATION = "destinationpath"
SERVICE_PARAM_FILENAME = "filename"
SERVICE_PARAM_FORMAT = "format"
SERVICE_PARAM_EXCLUDE = CONF_EXCLUDE
SERVICE_PARAM_BEGINTIME = "begintimestamp"
SERVICE_PARAM_TARGET_SIZE = "target_size"
SERVCE_PARAM_ENDTIME = "endtimestamp"
SERVICE_PARAM_LASTHOURS = "lasthours"
EPOCH_START = "01/01/1970 00:00:00"
EPOCH_END = "31/12/2037 23:59:59"

SNAPTOGIF_CREATE_SCHEMA = vol.Schema(
    {
        vol.Required(SERVICE_PARAM_SOURCE): cv.isdir,
        vol.Required(SERVICE_PARAM_DESTINATION): cv.isdir,
        vol.Optional(SERVICE_PARAM_FILENAME, default="latest"): cv.matches_regex(
            r'^[^<>:;,.?"*|/\\]+$'
        ),
        vol.Optional(SERVICE_PARAM_FORMAT, default="gif"): vol.In(["gif", "mp4"]),
        vol.Optional(SERVICE_PARAM_EXCLUDE, default=[]): cv.ensure_list_csv,
        vol.Optional(SERVICE_PARAM_BEGINTIME, default=EPOCH_START): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVCE_PARAM_ENDTIME, default=EPOCH_END): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVICE_PARAM_LASTHOURS, default=0.0): cv.positive_float,
        vol.Optional(SERVICE_PARAM_DELAY_TIME, default=1.0): cv.positive_float,
        vol.Optional(SERVICE_PARAM_MAX_FPS, default=60): cv.positive_float,
        vol.Optional(SERVICE_PARAM_TARGET_SIZE, default="0:0"): cv.matches_regex(r"[0-9]{1,6}:[0-9]{1,6}"),
    }
)

SNAPTOGIF_DEL_SCHEMA = vol.Schema(
    {
        vol.Required(SERVICE_PARAM_SOURCE): cv.isdir,
        vol.Optional(SERVICE_PARAM_EXCLUDE, default=[]): cv.ensure_list_csv,
        vol.Optional(SERVICE_PARAM_BEGINTIME, default=EPOCH_START): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVCE_PARAM_ENDTIME, default=EPOCH_END): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVICE_PARAM_LASTHOURS, default=0.0): cv.positive_float,
    }
)
SNAPTOGIF_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(SERVICE_PARAM_SOURCE): cv.isdir,
        vol.Required(SERVICE_PARAM_DESTINATION): cv.string,
        vol.Optional(SERVICE_PARAM_EXCLUDE, default=[]): cv.ensure_list_csv,
        vol.Optional(SERVICE_PARAM_BEGINTIME, default=EPOCH_START): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVCE_PARAM_ENDTIME, default=EPOCH_END): cv.matches_regex(
            r"[0-3][0-9]/[0-1][0-9]/\d{4} [0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
        ),
        vol.Optional(SERVICE_PARAM_LASTHOURS, default=0.0): cv.positive_float
    }
)


def Getfileslist(path, exclude, begintime, endtime, extensions, lasthours=0.0):
    def GetTimestampFile(path, file):
        return os.path.getmtime(os.path.join(path, file))

    # get files in source path
    files = os.listdir(path)
    # only files with selected extensions and filter out the excludelist
    files = [
        file
        for file in files
        if any(x in file for x in extensions) and file not in exclude
    ]

    # convert timestrings to epoch time
    BeginTimeStamp = time.mktime(
        datetime.datetime.strptime(begintime, "%d/%m/%Y %H:%M:%S").timetuple()
    )
    EndTimeStamp = time.mktime(
        datetime.datetime.strptime(endtime, "%d/%m/%Y %H:%M:%S").timetuple()
    )

    # filter files between timestamps
    files = [
        file
        for file in files
        if BeginTimeStamp <= GetTimestampFile(path, file) <= EndTimeStamp
    ]

    # sort images on modified date
    files.sort(key=lambda x: os.path.getmtime(os.path.join(path, x)))

    # only last xx hours filtering active
    if lasthours > 0.0 and len(files) > 1:
        # timestamp latest file in selected range
        latest = GetTimestampFile(path, files[-1])
        # Get images defined by lasthours from latest file
        files = [
            file
            for file in files
            if (latest - (lasthours * 3600)) <= GetTimestampFile(path, file) <= EndTimeStamp
        ]
    return files


def createOutputfile(hass, call, files):
    # convert selected range to selected format
    inputfolder = call.data[SERVICE_PARAM_SOURCE]
    outputfile = (
        f"{call.data[SERVICE_PARAM_FILENAME]}.{call.data[SERVICE_PARAM_FORMAT]}"
    )
    outputfolder = call.data[SERVICE_PARAM_DESTINATION]

    target_size = call.data[SERVICE_PARAM_TARGET_SIZE]
    if target_size:
        target_size = target_size.split(":")
        if target_size[0] == "0":
            target_size = None
        else:
            target_size = (int(target_size[0]), int(target_size[1]))

    target_fps = 1 / call.data[SERVICE_PARAM_DELAY_TIME]
    fps = min(target_fps, call.data[SERVICE_PARAM_MAX_FPS])

    percentage_of_fps_to_remove = 1 - (fps / target_fps)

    new_files = []
    remove_factor = 0
    for i in range(len(files)):
        remove_factor += percentage_of_fps_to_remove
        is_last_file = i == len(files) - 1
        if remove_factor >= 1 and not is_last_file:
            remove_factor -= 1
            continue
        new_files.append(files[i])

    files = new_files

    try:
        # sort images on modified date
        files.sort(key=lambda x: os.path.getmtime(os.path.join(inputfolder, x)))
        # convert frames to destination format (GIF/MP3)

        writer = imageio.get_writer(
            os.path.join(outputfolder, outputfile), mode="I", fps=fps
        )

        if not target_size:
            biggest_size = (0, 0, 0)
            for file in files:
                img = imageio.imread(os.path.join(inputfolder, file))
                if biggest_size[0] < img.shape[0]:
                    biggest_size = img.shape
    
            block_size = 16
            width = biggest_size[0] - (biggest_size[0] % block_size)
            height = biggest_size[1] - (biggest_size[1] % block_size)
    
            if height > width:
                (width, height) = (height, width)
        else:
            width = target_size[0]
            height = target_size[1]

        for file in files:
            try:
                img = imageio.imread(os.path.join(inputfolder, file))

                if img.shape[0] != width or img.shape[1] != height:
                    img = numpy.array(Image.fromarray(img).resize((width, height)))

                writer.append_data(img)
            except Exception as e:
                _LOGGER.warning(f"Error processing file and skipping: {file} error: {str(e)}")

        writer.close()

        _LOGGER.info(f"{outputfile} succesfully generated in: {outputfolder}")
        eventdata = {
            "type": SERVICE_CREATE,
            "file": outputfile,
            "destinationpath": outputfolder,
            "begintimestamp": call.data[SERVICE_PARAM_BEGINTIME],
            "endtimestamp": call.data[SERVCE_PARAM_ENDTIME],
            "no_files": len(files),
            "sourcepath": inputfolder,
            "sourcefiles": files,
        }
        hass.bus.fire(DOMAIN, eventdata)
    except Exception as e:
        _LOGGER.warning(
            f"Not able to store {outputfile} on given destination: {outputfolder} error:{str(e)}"
        )


def deletefiles(hass, call, files):
    # remove selected files
    inputfolder = call.data[SERVICE_PARAM_SOURCE]
    try:
        for file in files:
            os.remove(os.path.join(inputfolder, file))
        _LOGGER.info(f"Files succesfully removed from: {inputfolder}")
        eventdata = {
            "type": SERVICE_DEL,
            "begintimestamp": call.data[SERVICE_PARAM_BEGINTIME],
            "endTtimestamp": call.data[SERVCE_PARAM_ENDTIME],
            "no_files": len(files),
            "sourcepath": inputfolder,
            "sourcefiles": files,
        }
        hass.bus.fire(DOMAIN, eventdata)
    except Exception as e:
        _LOGGER.warning(
            f"Error deleting selected files on given destination: {inputfolder}\nerror:{str(e)}"
        )


def movefiles(hass, call, files):
    # move selected files
    inputfolder = call.data[SERVICE_PARAM_SOURCE]
    outputfolder = call.data[SERVICE_PARAM_DESTINATION]
    try:
        # create directory if not exist
        if not os.path.exists(outputfolder):
            os.makedirs(outputfolder)
        for file in files:
            shutil.move(os.path.join(inputfolder, file), outputfolder)
        _LOGGER.info(f"Files succesfully moved from: {inputfolder} to {outputfolder}")
        eventdata = {
            "type": SERVICE_MOVE,
            "begintimestamp": call.data[SERVICE_PARAM_BEGINTIME],
            "endtimestamp": call.data[SERVCE_PARAM_ENDTIME],
            "no_files": len(files),
            "sourcepath": inputfolder,
            "destinationpath": outputfolder,
            "sourcefiles": files,
        }
        hass.bus.fire(DOMAIN, eventdata)
    except Exception as e:
        _LOGGER.warning(
            f"Error moveing selected files on given source: {inputfolder}  to destination: {outputfolder}\nerror:{str(e)}"
        )


def setup(hass, config):
    # Set up is called when Home Assistant is loading our component.

    def GetTimestampFile(path, file):
        return os.path.getmtime(os.path.join(path, file))

    def Imagedirectory_Services(call):

        # get files in source path
        folder = call.data[SERVICE_PARAM_SOURCE]
        files = os.listdir(folder)

        "Allowed extentions for Servive start are jpg or png, for service move and delete also the possibile output extensions (gif, mp4) are allowed "
        if call.service == SERVICE_CREATE:
            ext = [".jpg", ".png"]
        else:
            ext = [".jpg", ".png", ".mp4", "gif"]

        # get files in source path and use the defined critera to filter the list
        files = Getfileslist(
            call.data[SERVICE_PARAM_SOURCE],
            call.data[SERVICE_PARAM_EXCLUDE],
            call.data[SERVICE_PARAM_BEGINTIME],
            call.data[SERVCE_PARAM_ENDTIME],
            ext,
            call.data[SERVICE_PARAM_LASTHOURS],
        )

        _LOGGER.debug(f"No of images/files found for operation {len(files)}")

        # Call the corresponding service
        if len(files) > 0:
            if call.service == SERVICE_CREATE:
                createOutputfile(hass, call, files)
            elif call.service == SERVICE_DEL:
                deletefiles(hass, call, files)
            elif call.service == SERVICE_MOVE:
                movefiles(hass, call, files)
        else:
            _LOGGER.warning(
                f"No files found in the specified time range: [{call.data[SERVICE_PARAM_BEGINTIME]} , {call.data[SERVCE_PARAM_ENDTIME]}] in :{folder}"
            )

    # register services to homeassistant
    hass.services.register(
        DOMAIN, SERVICE_CREATE, Imagedirectory_Services, schema=SNAPTOGIF_CREATE_SCHEMA
    )
    hass.services.register(
        DOMAIN, SERVICE_DEL, Imagedirectory_Services, schema=SNAPTOGIF_DEL_SCHEMA
    )
    hass.services.register(
        DOMAIN, SERVICE_MOVE, Imagedirectory_Services, schema=SNAPTOGIF_MOVE_SCHEMA
    )
    # Return boolean to indicate that initialization was successfully.
    return True

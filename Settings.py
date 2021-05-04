
import json, os, logging
logger = logging.getLogger(__name__)
# Note, this logger will be overridden due the logger
# itself using this module for a logging file name

class FileErrorHandler:
    """
    This class acts as a Context Manager for handling,
    guiding and modifying errors regarding the settings.json file.
    """
    def __init__(self):
        super().__init__()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            if exc_type in (ValueError, json.decoder.JSONDecodeError):
                # If there is a ValueError or json.decoder.JSONDecodeError,
                # we want to let the user know their settings.json file is incorrect.
                raise ValueError("There is an error in your settings file.")

            elif exc_type is FileNotFoundError:
                # If the file is missing, create a standardised settings.json file
                # With all parameters required.
                with open(Settings.PATH, "w") as f:
                    standard_dict = {
                                        "Host": "irc.chat.twitch.tv",
                                        "Port": 6667,
                                        "Channel": "#<channel>",
                                        "Nickname": "<name>",
                                        "Authentication": "oauth:<auth>",
                                        "AllowedRanks": [
                                            "broadcaster",
                                            "moderator",
                                            "vip"
                                        ],
                                        "AllowedUsers": []
                                    }
                    f.write(json.dumps(standard_dict, indent=4, separators=(",", ": ")))
                    raise ValueError("Please fix your settings.json file that was just generated.")
        return False

class Settings:
    """ Loads data from settings.json into the bot """

    PATH = os.path.dirname(__file__) + "/settings.json"
    #PATH = "/content/TwitchAIDungeon/settings.json"
    def __init__(self, bot):
        with FileErrorHandler():
            # Try to load the file using json.
            # And pass the data to the Bot class instance if this succeeds.
            logger.debug("Starting setting settings...")
            with open(Settings.PATH, "r") as f:
                settings = f.read()
                data = json.loads(settings)
                bot.set_settings(data["Host"],
                                data["Port"],
                                data["Channel"],
                                data["Nickname"],
                                data["Authentication"],
                                data["AllowedRanks"],
                                data["AllowedUsers"])
                logger.debug("Finished setting settings.")

    @staticmethod
    def get_channel():
        with FileErrorHandler():
            with open(Settings.PATH, "r") as f:
                settings = f.read()
                data = json.loads(settings)
                return data["Channel"].replace("#", "").lower()

    @staticmethod
    def set_logger():
        # Update logger. This is required as this class is used to set up the logging file
        global logger
        logger = logging.getLogger(__name__)

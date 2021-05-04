import multiprocessing as mp
from profanityfilter import ProfanityFilter
from TwitchWebsocket import TwitchWebsocket
from Settings import Settings
from Database import Database
from Log import Log
import time
from ctypes import c_bool, c_char
import threading
import logging
import os
import random
import datetime
import re

class VoteBot:
    def __init__(self, autovote=False, prompt="Chat \"!v (suggestion)\"!"):
        Settings.set_logger()
        self.host = None
        self.port = None
        self.auth = None
        capability = ["tags"]
        self.chan = None
        self.nick = None
        self.sending_message = True
        self.curr_prompt = prompt
        self.updated = mp.Value(c_bool, True)
        self.autovote = autovote
        self.log_results = True
        self.skip_voting = False
        self.random_collection = False
        self.collecting_time = 120
        self.voting_time = 120
        self.stream_delay = 2
        self.vote_cooldown = 120
        self.commands_collected_max = 5
        self.commands_collected = []
        self.votes_collected = []
        self.prompt = prompt
        self.min_msg_size = 5
        self.max_msg_size = 200

        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blacklist.txt"), "r") as f:
            censor = [l.replace("\n", "") for l in f.readlines()]
            self.pf = ProfanityFilter(custom_censor_list=censor)

        logging.debug("Setting settings.")
        Settings(self)

        logging.debug("Creating Database instance.")
        self.db = Database(self.chan)

        logging.debug("Creating TwitchWebsocket object.")
        self.ws = TwitchWebsocket(host=self.host,
                                  port=self.port,
                                  chan=self.chan,
                                  nick=self.nick,
                                  auth=self.auth,
                                  callback=self.message_handler,
                                  capability=capability,
                                  live=True)

        self.curr_mode = mp.Value(c_char, b's')
        logging.debug("Starting Websocket connection.")
        self.ws.start_blocking()

    def set_settings(self, host, port, chan, nick, auth, allowed_ranks, allowed_users):
        self.host, self.port, self.chan, self.nick, self.auth, self.allowed_ranks, self.allowed_users= host, port, chan, nick, auth, [rank.lower() for rank in allowed_ranks], [user.lower() for user in allowed_users]

    def not_bool(self, setting): # switches setting
        return not setting

    def extract_message(self, m):
        try:
            # Extract the message after the first space.
            return m.message[m.message.index(" ") + 1:]
        except ValueError:
            # If no spaces, return empty string
            return ""

    def check_permissions(self, m):
        # Gets users permissions for mod commands
        for rank in self.allowed_ranks:
            if rank in m.tags["badges"]:
                return True
        return m.user.lower() in self.allowed_users

    def check_mod_commands(self, m):
        if m.message.startswith("!cdtime"):
            # set cooldown between votes
            setting = self.is_int(m)
            if setting > 0: self.vote_cooldown = setting
            else: setting = self.vote_cooldown
            self.ws.send_message("Cooldown time (seconds): " + str(setting))

        elif m.message.startswith("!times"):
            # set all 3 phase times
            self.set_times(m)

        elif m.message.startswith("!vtime"):
            # set voting phase time
            setting = self.is_int(m)
            if setting > 0: self.voting_time = setting
            else: setting = self.voting_time
            self.ws.send_message("Voting time (seconds): " + str(setting))

        elif m.message.startswith(("!rand", "!random")):
            # sets random collection mode
            setting = self.not_bool(self.random_collection)
            self.random_collection = setting
            self.ws.send_message("Random Collection Mode: " + str(setting))

        elif m.message.startswith("!ctime"):
            # set collecting phase time
            setting = self.is_int(m)
            if setting > 0: self.collecting_time = setting
            else: setting = self.collecting_time
            self.ws.send_message("Collecting time (seconds): " + str(setting))

        elif m.message.startswith("!msg"):
            # set to have responses sent to chat
            setting = self.not_bool(self.sending_message)
            self.sending_message = setting
            self.ws.send_message("Sending chat messages: " + str(setting))

        elif m.message.startswith("!stop"):
            # end voting, remove HTML
            self.stop_vote()

        elif m.message.startswith("!start"):
            # start voting, display HTML
            self.begin_voting(self.extract_message(m), False)

        elif m.message.startswith("!autovote"):
            # turns on/off autovote
            setting = self.not_bool(self.autovote)
            self.autovote = setting
            self.ws.send_message("Autovote mode: " + str(setting))

        elif m.message.startswith("!max"):
            # sets max amount of suggestions
            setting = self.is_int(m)
            if setting > 0: self.commands_collected_max = setting
            else: setting = self.commands_collected_max
            self.ws.send_message("Max candidates: " + str(setting))

        elif m.message.startswith("!dtime"):
            # sets stream delay time
            setting = self.is_int(m)
            if setting > 0: self.stream_delay = setting
            else: setting = self.stream_delay
            self.ws.send_message("Stream delay (seconds): " + str(setting))

        elif m.message.startswith("!ballot"):
            # sends ballot.txt info to vote panel (optionally send custom vote phase time)
            self.send_ballot(m)

        elif m.message.startswith("!clear"):
            # stop voting and clear HTML
            self.clear_tables()

        elif m.message.startswith("!skip"):
            # turn on/off the voting phase
            setting = self.not_bool(self.skip_voting)
            self.skip_voting = setting
            self.ws.send_message("Skipping voting phase: " + str(setting))

        elif m.message.startswith("!r"):
            # removes a vote suggestion
            self.mod_remove_vote(m)

        elif m.message.startswith("!s"):
            # sets new default prompt and starts a vote
            # self.mod_command_send_next(self.extract_message(m))
            self.begin_voting(self.extract_message(m), True)

        else:
            return False

        return True

    def clear_html(self, m):
        # gets rid of html tags
        return m.replace("<", "").replace(">", "")

    def message_handler(self, m):
        if m.type == "366":
            logging.info(f"Successfully joined channel: #{m.channel}")
        elif m.type == "PRIVMSG":
            if self.check_permissions(m) and self.check_mod_commands(m): # check if command is a mod command first
                return
            elif m.message.lower().startswith(("!v", "!vote")): # main voting command
                if self.curr_mode.value in (b'r', b'c'): # if ready to collect or currently collecting
                    self.vote_command(m.user, self.clear_html(self.extract_message(m)).strip())
                elif self.curr_mode.value == b'v': # if in voting phase
                    vote = self.extract_message(m)
                    self.cast_vote(m.user, vote)

            elif self.curr_mode.value == b'v': # if in voting phase
                vote = m.message.strip()
                self.cast_vote(m.user, vote)

    def is_int(self, m):
        try:
            setting = int(self.extract_message(m))
            return setting
        except ValueError:
            self.ws.send_whisper(m.user, "Not a valid int.")
            return -1

    def set_times(self, m): # sets collecting, voting, and cooldown times
        def_times = [self.collecting_time, self.voting_time, self.vote_cooldown]
        try:
            times = self.extract_message(m).split()
            for i, time in enumerate(times):
                if i < len(def_times):
                    def_times[i] = int(time)
            # 1 to 3 numbers can be provided to set the times
            # 1st: collecting time, 2nd: voting time, 3rd: cooldown time
            self.collecting_time = def_times[0]
            self.voting_time = def_times[1]
            self.vote_cooldown = def_times[2]
            self.ws.send_message("Times (seconds) - Collection: " + str(self.collecting_time) + " | Voting: " + str(self.voting_time) + " | Cooldown: " + str(self.vote_cooldown))

        except Exception:
            print(Exception, ": Invalid times.")

    def clear_tables(self):
        self.curr_mode.value = b'l'
        self.display_clear()

    def mod_remove_vote(self, m): # used to clear a row to exclude it from the current collecting/voting phases
        pos = int(self.extract_message(m))
        if pos > 0 and pos <= len(self.commands_collected):
            if self.commands_collected[pos-1]:
                self.commands_collected[pos-1][2] = False

    def begin_voting(self, prompt=None, set_default=False):
        if self.curr_mode.value in (b'a', b's', b'l'):
            if prompt in ("", None):
                prompt = self.prompt
            elif set_default:
                self.prompt = prompt

            self.commands_collected = []
            self.votes_collected = []
            self.curr_prompt = prompt
            self.curr_mode.value = b'r'
            self.updated.value = True
            self.display_vote_start(prompt)
            return

        self.ws.send_message("Current vote isn't finished!")

    def stop_vote(self):
        self.curr_mode.value = b's'
        self.display_vote_stop()

    def display_vote_start(self, prompt):
        if self.sending_message:
            self.ws.send_message("Starting vote!")
            self.ws.send_message(prompt)

    def display_vote_stop(self):
        pass

    def display_clear(self):
        pass

    def send_ballot(self, m):
        # ballot.txt prompt and vote options set to be voted on
        # ballot.txt: line 1: prompt
        # other lines: suggestions to be voted on
        # any number sent with the command sets the vote phase time
        vote_time = self.is_int(m)
        if vote_time < 1:
            vote_time = self.voting_time

        if self.curr_mode.value in (b's', b'l'):
            self.commands_collected = []
            self.votes_collected = []
            f = open(os.getcwd() + "/ballot.txt")
            ballot = f.readlines()
            self.curr_prompt = ballot[0].strip()
            self.commands_collected = []
            for item in ballot[1:]:
                self.commands_collected.append([item.strip(), 0, True, "ballot"])

            self.updated.value = False
            self.curr_mode.value = b'v'
            self.display_vote_start(prompt_class="voting-prompt")
            self.start_vote_collector(False, False, vote_time)

    def mod_command_set_autovote(self, m):
        msg = self.extract_message(m)
        if msg.lower() in ("false", "0", "f"):
            self.autovote = False
        elif msg == "":
            self.autovote = self.not_bool(self.autovote)
        else:
            self.autovote = True

    def get_list_vote(self, key, list):
        for index, x in enumerate(list):
            if key.lower() == x[0].lower():
                return [index, x[1]]
        return False

    def mod_command_send_msg(self, m):
        msg = self.extract_message(m)
        if msg.lower() == "true":
            self.sending_message = True
        elif msg.lower() == "false":
            self.sending_message = False
        else:
            print("Failed to set send_msg value.")

    def command_cooldown(self, m):
        try:
            msg = int(self.extract_message(m))
            if msg > 0:
                self.cooldown = msg
                print("Changed cooldown to " + str(msg) + " seconds.")
            else:
                print("Error: Cooldown cannot be negative.")
        except:
            print("Error: Cooldown value invalid.")

    def censor(self, message):
        # Replace banned phrase with ***
        censored = self.pf.censor(message)
        if message != censored:
            logging.warning(f"Censored \"{message}\" into \"{censored}\".")
        return censored

    def vote_command(self, user, message): # Send a candidate to be voted on - or add to vote of already suggested one
        message = self.censor(message)
        ml = len(message)
        if ml >= self.min_msg_size and ml <= self.max_msg_size:
            if self.curr_mode.value == b'r':
                self.start_collecting()
            if self.curr_mode.value == b'c' and len(self.commands_collected) < self.commands_collected_max: # check if commands are still allowed
                self.add_command(user, message)

                if len(self.commands_collected) == self.commands_collected_max:
                    self.curr_mode.value = b'v'

                self.updated.value = False
            elif self.curr_mode.value == b'x': # random collection phase
                self.add_command(user, message)
        else: self.ws.send_message("@" + str(user) + " - Your message must be between " + str(self.min_msg_size) + " and " + str(self.max_msg_size) + " characters long.")

    def add_command(self, user, message):
        user_vote = self.get_list_vote(user, self.votes_collected)
        # if user has voted, check if the candidate exists and switch vote if it does
        # else, add candidate to list
        command_vote = self.get_list_vote(message, self.commands_collected)
        if command_vote: # if command exists, change user vote
            if user_vote:
                prev_vote = int(self.votes_collected[user_vote[0]][1]) - 1
                self.commands_collected[prev_vote][1] -= 1 # change previous vote count
                self.commands_collected[command_vote[0]][1] += 1 # increase vote count of selection
                self.votes_collected[user_vote[0]][1] = user_vote[1] # change vote selection
            else:
                self.commands_collected[command_vote[0]][1] += 1
        else:
            if user_vote:
                self.ws.send_message("@" + str(user) + " - You've already submitted a candidate and cannot submit another this round.")
                pass
            else:
                self.commands_collected.append([message, 1, True, user])
                self.votes_collected.append([user, len(self.commands_collected) - 1])

    def start_collecting(self): # on receiving first command, start the collecting timer
        if self.random_collection:
            self.curr_mode.value = b'x'
        else:
            self.curr_mode.value = b'c'
        command_collector = threading.Thread(target=self.command_collector,args=(self.curr_mode.value,),daemon=True)
        command_collector.start()

    def start_vote_collector(self, autovote, skip, timer):
        vote_collector = threading.Thread(target=self.vote_collector,args=(autovote,skip,timer),daemon=True)
        vote_collector.start()

    def command_collector(self, mode):
        self.wait_for_updates(self.collecting_time, mode, "collecting-prompt", skip_voting=self.skip_voting)
        if not self.curr_mode.value in (b's', b'l'): # start voting phase if not stopped
            if self.random_collection:
                self.get_random_commands()

            self.start_vote_collector(self.autovote, self.skip_voting, self.voting_time)
        elif self.log_results: # log results if collection stops before voting phase
            self.save_vote_log()

    def cast_vote(self, user, vote): # if vote is valid, add it to tally
        try:
            vote = int(vote)
        except ValueError:
            return

        vote_num = vote-1
        if vote_num < len(self.commands_collected):
            user_vote = self.get_list_vote(user, self.votes_collected)

            # if user has voted, change their vote selection and previous vote total
            if user_vote:
                self.commands_collected[user_vote[1]][1] -= 1
                self.votes_collected[user_vote[0]][1] = vote_num
            else:
                # else, add new vote
                self.votes_collected.append([user, vote_num])

            # increase vote count of selection
            self.commands_collected[vote_num][1] += 1
            self.updated.value = False

    def get_random_commands(self):
        # copies the current command list, and cuts it down to its max size with random items popped from it
        if len(self.commands_collected) > self.commands_collected_max:
            new_list = self.commands_collected[:]
            self.commands_collected = []
            selections = [*range(len(new_list))]
            for i in range(self.commands_collected_max):
                cmd_num = selections.pop(random.choice(range(len(selections))))
                for pos, vote in enumerate(self.votes_collected):
                    # changes votes to its new location on the list
                    if vote[1] == cmd_num: self.votes_collected[pos][1] = i + 1

                self.commands_collected.append(new_list[cmd_num])

            self.updated.value = False

    def votecount(self, a): # used for sorting the completed vote list
        return a[1]

    def wait_for_updates(self, duration, updating):
        start_time = time.time()
        i = duration
        delay = duration + self.stream_delay
        while i < delay and updating.value:
            time.sleep(1)
            i += 1

    def display_final_results(self):
        winner = self.get_winner()

        if self.sending_message:
            self.ws.send_message(winner_msg)
        else:
            print(winner_msg)

    def get_winner(self):
        if len(self.commands_collected) > 0 and not self.skip_voting:
            results_list = [x for x in self.commands_collected[:] if x[2]]
            results_list.sort(key=self.votecount, reverse=True)
            winner_list = []
            top_votes = results_list[0][1]
            for index, item in enumerate(self.commands_collected):
                if item[1] == top_votes:
                    winner_list.append([item, index])

            winner_msg = "Winner: "
            if len(winner_list) > 1:
                winner_msg += "Tie breaker - "
                winner = random.choice(winner_list)
            else:
                winner = winner_list[0]

            winner_msg +=  winner[0][0] + " | votes: " + str(winner[0][1])

            if self.sending_message:
                self.ws.send_message(winner_msg)
            else:
                print(winner_msg)

            return winner
        return False


    def wait_duration(duration, updating):
        pass

    def change_prompt(prompt, prompt_class):
        pass

    # saves votes and timestamps after a completed vote
    def save_vote_log(self):
        path = os.getcwd() + "/vote_logs.txt"
        try:
            timestamp = datetime.datetime.now()
            f = open(path, "a", encoding="utf-8")
            cmd_text = "\n" + "--" + str(timestamp) + "--" + "\n"
            cmd_text += self.curr_prompt + "\n" + "--------------" + "\n"
            for cmd in self.commands_collected:
                if not cmd[2]:
                    cmd_text += "**REMOVED** "

                cmd_text += cmd[0] + " - " + cmd[3]
                if not self.skip_voting:
                    cmd_text += " | votes: " + str(cmd[1])

                cmd_text += "\n"
            f.write(cmd_text)

        except:
            print("Failed saving vote log.")

    # displays candidates and time left (+stream delay) for voting
    def vote_collector(self, autovote, skip_voting, vote_timer):
        self.curr_mode.value = b'v'
        if not skip_voting:
            if len(self.commands_collected) > 1: # only vote if there is more than 1 item
                if self.sending_message:
                    self.ws.send_message("Type the number of the item to cast a vote!")
                self.wait_for_updates(vote_timer, b'v', "voting-prompt", skip_voting=skip_voting)

            if not self.curr_mode.value in (b's', b'l'):
                self.display_final_results()

        if self.log_results: # saves results to vote_logs.txt
            self.save_vote_log()

        if autovote and not self.curr_mode.value in (b's', b'l') : # autovote check, turns off if next vote starts
            self.curr_mode.value = b'a'
            self.wait_for_updates(self.vote_cooldown, b'a', "cooldown-prompt", use_delay=False, skip_voting=skip_voting)
            if self.curr_mode.value == b'a' and autovote:
                self.begin_voting()

if __name__ == "__main__":
    VoteBot()

import logging
import arrow
import json
import os
import re
from os.path import join, abspath, dirname

import settings
import github_api as gh

THIS_DIR = dirname(abspath(__file__))
# Hopefully this isn't overwritten on pulls
SAVED_COMMANDS_FILE = join(THIS_DIR, '..', "server/issue_commands_ran.json")
if not os.path.exists(SAVED_COMMANDS_FILE):
    '''
        "comment_id": [ // the actual id
            {
                "comment_id" : int,
                "issue_id": int,
                "has_ran" : bool,
                "command": string
                "chaos_response_id" : int, // chaos status comment id
                "time_remaining": int
            }

        "last_ran": "2017-05-20T00:00:00Z" //format
        ]
    '''
    with open(SAVED_COMMANDS_FILE, 'w') as f:
        json.dump({}, f)

__log = logging.getLogger("poll_issue_commands")

'''
Command Syntax
/vote close closes issue when no nay reactions on this comment are added within voting window
/vote reopen reopens issue when see above ^^^
/vote label=<LABEL_TEXT> adds label when ^^^
/vote remove-label=<LABEL_TEXT> removes label when ^^^
/vote assign=<USER> assigns to user when ^^^
/vote unassign=<USER> unassigns from user when ^^^
'''

# If no subcommands, map cmd: None
COMMAND_LIST = {
        "/vote": ("close", "reopen")
    }


def update_db(comment_id, data_fields, db=None):

    if not db:
        with open(SAVED_COMMANDS_FILE, 'r') as f:
            db = json.load(f)

    if comment_id not in db:
        db[comment_id] = {}

    for field, value in data_fields.items():
        db[comment_id][field] = value

    with open(SAVED_COMMANDS_FILE, 'w') as f:
        json.dump(db, f)

    return db


def select_db(comment_id, fields, db=None):
    if db is None:
        with open(SAVED_COMMANDS_FILE, 'r') as f:
            db = json.load(f)
    return {field: db[comment_id][field] for field in fields}


def get_last_ran(db=None):
    if db is None:
        with open(SAVED_COMMANDS_FILE, 'r') as f:
            db = json.load(f)
    return db.get("last_ran", None)  # First time return none


def set_last_run(last_ran, db=None):
    if db is None:
        with open(SAVED_COMMANDS_FILE, 'r') as f:
            db = json.load(f)

    db["last_ran"] = last_ran
    with open(SAVED_COMMANDS_FILE, 'w') as f:
        json.dump(db, f)


def insert_or_update(api, comment_id, issue_id, comment_txt):
    command_history = {}
    with open(SAVED_COMMANDS_FILE, 'r') as f:
        command_history = json.load(f)

    # equivalent of db INSERT OR UPDATE.
    comment_data = command_history.get(comment_id, None)

    if not comment_data:
        comment_data = {
            "comment_id": comment_id,
            "issue_id": issue_id,
            "has_ran": False,
            # "command": comment_txt,
            "chaos_response_id": None,
            "time_remaining": None
        }
        command_history[comment_id] = comment_data

    if comment_data["has_ran"]:
        return

    voting_window = gh.voting.get_initial_voting_window()

    seconds_remaining = gh.issues.voting_window_remaining_seconds(api, settings.URN, comment_id,
                                                                  voting_window)
    seconds_remaining = max(0, seconds_remaining)  # No negative time
    data = {
        "time_remaining": seconds_remaining,
        "command": comment_txt  # Keep this fresh so nobody edits their command post..
        }
    update_db(comment_id, data, db=command_history)


def has_enough_votes(votes):
            # __log.debug("vote less than one")
    return all(vote >= 0 for user, vote in votes.items())


def post_command_status_update(api, issue_id, comment_id, has_votes):

    # First find out if we have posted a status update for this command already
    # Todo, stop opening all these files
    command_history = {}
    with open(SAVED_COMMANDS_FILE, 'r') as f:
        command_history = json.load(f)

    # Todo - stop doing loops
    comment_data = command_history[comment_id]
    if comment_data["has_ran"]:
        return

    seconds_remaining = comment_data["time_remaining"]
    command_text = comment_data["command"]

    time = gh.misc.seconds_to_human(seconds_remaining)
    status = "passing" if has_votes else "failing"
    body = "> {command}\n\nTime remaining: {time} - Vote status: {status}".format(
                                                                            command=command_text,
                                                                            time=time,
                                                                            status=status)

    if comment_data["chaos_response_id"]:
        resp = gh.comments.edit_comment(api, settings.URN, comment_data["chaos_response_id"], body)
    else:
        resp = gh.comments.leave_comment(api, settings.URN, issue_id, body)
        update_db(comment_id, {"chaos_response_id": str(resp["id"])}, db=command_history)


def can_run_vote_command(api, comment_id):
    comment_data = select_db(comment_id, ("has_ran", "time_remaining"))

    if comment_data["has_ran"]:
        # __log.debug("Already ran command")
        return False

    time_left = comment_data["time_remaining"]
    return time_left <= 0


def update_command_ran(api, comment_id, text):
    db = update_db(comment_id, {"has_ran": True})
    db_fields = select_db(comment_id, ("chaos_response_id", "command"), db=db)
    resp_id = db_fields["chaos_response_id"]
    command = db_fields["command"]
    body = "> {command}\n\n{text}".format(command=command, text=text)
    gh.comments.edit_comment(api, settings.URN, resp_id, body)


def get_command_votes(api, urn, comment_id):
    return {
        voter: vote
        for voter, vote in gh.voting.get_comment_reaction_votes(
            api, urn, comment_id
        )
    }


def handle_vote_command(api, command, issue_id, comment_id, votes):
    orig_command = command[:]
    # Check for correct command syntax, ie, subcommands
    log_warning = False
    if len(command):
        sub_command = command.pop(0)
        if sub_command == "close":
            gh.issues.close_issue(api, settings.URN, issue_id)
            gh.comments.leave_issue_closed_comment(api, settings.URN, issue_id)
        elif sub_command == "reopen":
            gh.issues.open_issue(api, settings.URN, issue_id)
            gh.comments.leave_issue_reopened_comment(api, settings.URN, issue_id)
    else:
        log_warning = True

    if log_warning:
        __log.warning("Unknown issue command syntax: /vote {command}".format(command=orig_command))


def handle_comment(api, issue_comment):
    issue_id = issue_comment["issue_id"]
    global_comment_id = str(issue_comment["global_comment_id"])
    comment_text = issue_comment["comment_text"]

    comment_text = re.sub('\s+', ' ', comment_text)
    parsed_comment = list(map(lambda x: x.lower(), comment_text.split(' ')))
    command = parsed_comment.pop(0)

    if command in COMMAND_LIST:
        votes = get_command_votes(api, settings.URN, global_comment_id)
        insert_or_update(api, global_comment_id, issue_id, comment_text)
        can_run = can_run_vote_command(api, global_comment_id)
        has_votes = has_enough_votes(votes)
        post_command_status_update(api, issue_id, global_comment_id, has_votes)

        # We doin stuff boyz
        if can_run and has_votes:
            __log.debug("Handling issue {issue}: command {comment}".format(issue=issue_id,
                                                                           comment=comment_text))

            if command == "/vote":
                handle_vote_command(api, parsed_comment, issue_id, global_comment_id, votes)

            update_command_ran(api, global_comment_id, "Command Ran")

        elif can_run:
            # oops we didn't pass
            update_command_ran(api, global_comment_id, "Vote Failed")


def is_command(comment):
    comment = re.sub('\s+', ' ', comment)
    parsed_comment = list(map(lambda x: x.lower(), comment.split(' ')))
    cmd = parsed_comment[0]
    is_cmd = False

    if cmd in COMMAND_LIST:
        subcommands = COMMAND_LIST.get(cmd, None)

        # 4 cases
        # 1. No subcommands for command
        # 2. Subcommands exist, and args has it
        # 3. Subcommands exist, and args don't have it
        # 4. Args specify non existant subcommand
        if subcommands is None:
            is_cmd = True  # Already have the command
        else:
            sub_cmd_with_args = parsed_comment[1:]

            if len(sub_cmd_with_args) > 0:
                sub_cmd = sub_cmd_with_args[0]

                # Check cond 2
                is_cmd = sub_cmd in subcommands
            else:
                # Cond 3
                is_cmd = False

    return is_cmd


def poll_read_issue_comments(api):
    __log.info("looking for issue comments")

    last_ran = get_last_ran()
    if last_ran:
        last_ran = arrow.get(last_ran)

    paged_results = gh.comments.get_all_issue_comments(api,
                                                       settings.URN,
                                                       page='all',
                                                       since=last_ran)

    # This now only finds new entries that have been either posted or updated
    # Add them to our database
    # If page=all, you have to loop through pages as well
    for page in paged_results:
        for issue_comment in page:
            # Get info and store in db
            issue_id = issue_comment["issue_id"]
            global_comment_id = str(issue_comment["global_comment_id"])
            comment_text = issue_comment["comment_text"]
            if is_command(comment_text):
                insert_or_update(api, global_comment_id, issue_id, comment_text)

    # WARNING - be careful of saving wrong version of db to disk
    db = None
    with open(SAVED_COMMANDS_FILE, 'r') as f:
        db = json.load(f)

    # NEVER delete a comment_id data structure, even if it already ran
    # Simply updating the command comment will cause it to reenter the system
    # whic could cause unexpected behaviour
    # One solution is to delete the original comment with the command..
    # Or move id to a separate db
    if "last_ran" in db:
        del db["last_ran"]

    # TODO - run commands oldest to newest

    db_sorted = sorted(db.items(), key=lambda x: x[1]["time_remaining"])
    for cmd_id, cmd_obj in db_sorted:

        try:
            # I'm really lazy right now. Just mock up an object and pass to handle_comment
            mock = {
                "issue_id": cmd_obj["issue_id"],
                "global_comment_id": cmd_obj["comment_id"],
                "comment_text": cmd_obj["command"]
            }
            handle_comment(api, mock)
        except KeyError as e:
            __log.warning("Unable to handle comment id {id}".format(cmd_id))

    now = arrow.utcnow()
    set_last_run(gh.misc.dt_to_github_dt(now))

    __log.info("Waiting %d seconds until next scheduled Issue comment polling",
               settings.ISSUE_COMMENT_POLLING_INTERVAL_SECONDS)

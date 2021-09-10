import settings
import arrow
from . import prs
from .misc import handle_pagination_all, dt_to_github_dt


@handle_pagination_all
def get_all_issue_comments(api, urn, page=1, since=None):

    if since is None:
        # Set since to be before repo was created
        since = arrow.get("2017-05-20T00:00:00Z")

    # Do all issue comments at once for API's sake..
    path = "/repos/{urn}/issues/comments".format(urn=urn)
    # TODO - implement parameters
    # sort - Either "created" or "updated". Defautl: "created
    # direction - Either "asc" or "desc". Ignored without the sort parameter.
    # since - Only comments updated at or after this time are returned.
    # This is a timestamp in ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ.
    # Add get-reaction support for issue comments
    params = {
        "per_page": settings.DEFAULT_PAGINATION,
        "page": page
        }

    data = {
        "since": dt_to_github_dt(since)
    }

    comments = api("get", path, json=data, params=params)
    for comment in comments:
        # Return issue_id, global_comment_id, comment_text
        issue_comment = {'issue_id': comment["html_url"].split("/")[-1].split("#")[0]}
        # I believe this is the right one... Could also be issue specific comment id
        issue_comment["global_comment_id"] = comment["id"]
        issue_comment["comment_text"] = comment["body"]
        yield issue_comment


def get_reactions_for_comment(api, urn, comment_id):
    path = "/repos/{urn}/issues/comments/{comment}/reactions"\
        .format(urn=urn, comment=comment_id)
    params = {"per_page": settings.DEFAULT_PAGINATION}
    reactions = []

    try:
        reactions = api("get", path, params=params)
    except:
        pass

    yield from reactions


def leave_reject_comment(api, urn, pr, votes, total, threshold, meritocracy_satisfied):
    votes_summary = prs.formatted_votes_summary(votes, total, threshold, meritocracy_satisfied)
    body = """
:no_good: PR rejected {summary}.

Open a new PR to restart voting.
    """.strip().format(summary=votes_summary)
    return leave_comment(api, urn, pr, body)


def leave_accept_comment(api, urn, pr, sha, votes, total, threshold, meritocracy_satisfied):
    votes_summary = prs.formatted_votes_summary(votes, total, threshold, meritocracy_satisfied)
    body = """
:ok_woman: PR passed {summary}.

See merge-commit {sha} for more details.
    """.strip().format(summary=votes_summary, sha=sha)
    return leave_comment(api, urn, pr, body)


def leave_stale_comment(api, urn, pr, hours):
    body = """
:no_good: This PR has merge conflicts, and hasn't been touched in {hours} hours. Closing.

Open a new PR with the merge conflicts fixed to restart voting.
    """.strip().format(hours=hours)
    return leave_comment(api, urn, pr, body)


def leave_ci_failed_comment(api, urn, pr, hours):
    body = """
:no_good: This PR has failed to pass CI, and hasn't been touched in {hours} hours. Closing.

Open a new PR with the problems fixed to restart voting.
    """.strip().format(hours=hours)
    return leave_comment(api, urn, pr, body)


def leave_deleted_comment(api, urn, pr):
    body = """
:no_good: The repository backing this PR has been deleted.

Open a new PR with these changes to try again.
    """.strip()
    return leave_comment(api, urn, pr, body)


def leave_issue_closed_comment(api, urn, issue):
    body = ":no_entry: The issue has been closed after a vote."
    return leave_comment(api, urn, issue, body)


def leave_issue_reopened_comment(api, urn, issue):
    body = ":recycle: The issue has been reopened after a vote."
    return leave_comment(api, urn, issue, body)


def leave_comment(api, urn, pr, body):
    path = "/repos/{urn}/issues/{pr}/comments".format(urn=urn, pr=pr)
    data = {"body": body}
    return api("post", path, json=data)


def edit_comment(api, urn, comment_id, body):
    path = "/repos/{urn}/issues/comments/{id}".format(urn=urn, id=comment_id)
    data = {"body": body}
    return api("patch", path, json=data)

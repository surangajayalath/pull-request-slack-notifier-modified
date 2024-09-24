#!/usr/bin/env python3

import sys
import os
import json
import requests

################################################################################
# Helper Functions
################################################################################

def get_envar(name):
    """
    Given a name, return the corresponding environment variable. Exit if not
    defined, as using this function indicates the envar is required.

    Parameters:
    name (str): the name of the environment variable
    """
    value = os.environ.get(name)
    if not value:
        sys.exit("%s is required for surangajayalath/pull-request-slack-notifier-modified@v3" % name)
    return value


def check_events_json():
    """The GitHub events JSON is required to indicate that we are in an action environment."""
    events = get_envar("GITHUB_EVENT_PATH")
    if not os.path.exists(events):
        sys.exit("Cannot find Github events file at ${GITHUB_EVENT_PATH}")
    print("Found ${GITHUB_EVENT_PATH} at %s" % events)
    return events


def abort_if_fail(response, reason):
    """If PASS_ON_ERROR, don't exit. Otherwise exit with an error and print the reason.

    Parameters:
    response (requests.Response) : an unparsed response from requests
    reason                 (str) : a message to print to the user for fail.
    """
    message = "%s: %s: %s\n %s" % (
        reason,
        response.status_code,
        response.reason,
        response.json(),
    )

    if os.environ.get("PASS_ON_ERROR"):
        print("Error, but PASS_ON_ERROR is set, continuing: %s" % message)
    else:
        sys.exit(message)


def parse_into_list(values):
    """A list of reviewers or assignees to parse from a string to a list.

    Parameters:
    values (str) : a list of space-separated, quoted values to parse to a list
    """
    if values:
        values = values.replace('"', "").replace("'", "")
    if not values:
        return []
    return [x.strip() for x in values.split(" ")]


def set_env_and_output(name, value):
    """Helper function to echo a key/value pair to the environment file.

    Parameters:
    name (str)  : the name of the environment variable
    value (str) : the value to write to the file
    """
    for env_var in ("GITHUB_ENV", "GITHUB_OUTPUT"):
        environment_file_path = os.environ.get(env_var)
        if not environment_file_path:
            print(f"Warning: {env_var} is unset, skipping.")
            continue
        print("Writing %s=%s to %s" % (name, value, env_var))

        with open(environment_file_path, "a") as environment_file:
            environment_file.write("%s=%s\n" % (name, value))


def open_pull_request(title, body, target, source, is_draft=False, can_modify=True):
    """Open a pull request with a given body and content, and sets output variables.

    Parameters:
    title       (str) : the title to set for the new pull request
    body        (str) : the body to set for the new pull request
    target      (str) : the target branch
    source      (str) : the source branch
    is_draft   (bool) : indicate the pull request is a draft
    can_modify (bool) : indicate the maintainer can modify
    """
    print("No pull request from %s to %s is open, continuing!" % (source, target))

    # Post the pull request
    data = {
        "title": title,
        "body": body,
        "base": target,
        "head": source,
        "draft": is_draft,
        "maintainer_can_modify": can_modify,
    }
    print("Data for opening pull request: %s" % data)
    response = requests.post(PULLS_URL, json=data, headers=HEADERS)
    if response.status_code != 201:
        print(f"pull request url is {PULLS_URL}")
        abort_if_fail(response, "Unable to create pull request")

    return response


def update_pull_request(entry, title, body, target, state=None):
    """Given an existing pull request, update it.

    Parameters:
    entry      (dict) : the pull request metadata
    title       (str) : the title to set for the new pull request
    body        (str) : the body to set for the new pull request
    target      (str) : the target branch
    state      (bool) : the state of the PR (open, closed)
    """
    print("PULL_REQUEST_UPDATE is set, updating existing pull request.")

    data = {
        "title": title,
        "body": body,
        "base": target,
        "state": state or "open",
    }
    # PATCH /repos/{owner}/{repo}/pulls/{pull_number}
    url = "%s/%s" % (PULLS_URL, entry.get("number"))
    print("Data for updating pull request: %s" % data)
    response = requests.patch(url, json=data, headers=HEADERS)
    if response.status_code != 200:
        abort_if_fail(response, "Unable to update pull request")

    return response


def set_pull_request_groups(response):
    """Given a response for an open or updated PR, set metadata.

    Parameters:
    response (requests.Response) : a requests response, unparsed
    """
    # Expected return codes are 0 for success
    pull_request_return_code = (
        0 if response.status_code == 201 else response.status_code
    )
    response = response.json()
    print("::group::github response")
    print(response)
    print("::endgroup::github response")
    number = response.get("number")
    html_url = response.get("html_url")
    print("Number opened for PR is %s" % number)
    set_env_and_output("PULL_REQUEST_NUMBER", number)
    set_env_and_output("PULL_REQUEST_RETURN_CODE", pull_request_return_code)
    set_env_and_output("PULL_REQUEST_URL", html_url)


def list_pull_requests(target, source):
    """Given a target and source, return a list of pull requests that match (or simply exit given some kind of error code).

    Parameters:
    target (str) : the target branch
    source (str) : the source branch
    """
    # Check if the branch already has a pull request open
    params = {"base": target, "head": source, "state": "open"}
    print("Params for checking if pull request exists: %s" % params)
    response = requests.get(PULLS_URL, params=params)

    # Case 1: 401, 404 might warrant needing a token
    if response.status_code in [401, 404]:
        response = requests.get(PULLS_URL, params=params, headers=HEADERS)
    if response.status_code != 200:
        abort_if_fail(response, "Unable to retrieve information about pull requests")

    return response.json()


def add_assignees(entry, assignees):
    """Given a pull request metadata (from create or update) add assignees.

    Parameters:
    entry (dict)    : the pull request metadata
    assignees (str) : comma-separated assignees string set by action
    """
    # Remove leading and trailing quotes
    assignees = parse_into_list(assignees)
    number = entry.get("number")

    print(
        "Attempting to assign %s to pull request with number %s" % (assignees, number)
    )

    # POST /repos/:owner/:repo/issues/:issue_number/assignees
    data = {"assignees": assignees}
    ASSIGNEES_URL = "%s/%s/assignees" % (ISSUE_URL, number)
    response = requests.post(ASSIGNEES_URL, json=data, headers=HEADERS)
    if response.status_code != 201:
        abort_if_fail(response, "Unable to create assignees")

    assignees_return_code = 0 if response.status_code == 201 else response.status_code
    print("::group::github assignees response")
    print(response.json())
    print("::endgroup::github assignees response")
    set_env_and_output("ASSIGNEES_RETURN_CODE", assignees_return_code)


def find_pull_request(listing, source):
    """Given a listing and a source, find a pull request based on the source (the branch name).

    Parameters:
    listing (list) : the list of PR objects (dict) to parse over
    source   (str) : the source (head) branch to look for
    """
    if listing:
        for entry in listing:
            if entry.get("head", {}).get("ref", "") == source:
                print("Pull request from %s is already open!" % source)
                return entry


def find_default_branch():
    """Find default branch for a repo (only called if branch not provided)"""
    response = requests.get(REPO_URL)

    # Case 1: 401, 404 might need a token
    if response.status_code in [401, 404]:
        response = requests.get(REPO_URL, headers=HEADERS)
    if response.status_code != 200:
        abort_if_fail(response, "Unable to retrieve default branch")

    default_branch = response.json()["default_branch"]
    print("Found default branch: %s" % default_branch)
    return default_branch


def add_reviewers(entry, reviewers, team_reviewers):
    """Given regular and team reviewers, add them to the pull request.

    Parameters:
    entry (dict)           : the pull request metadata
    reviewers (str)        : reviewers to add
    team_reviewers (str)   : team reviewers to add
    """
    # Get the pull request number
    number = entry.get("number")

    # Get the reviewers
    reviewers = parse_into_list(reviewers)
    team_reviewers = parse_into_list(team_reviewers)
    print("Adding reviewers %s and teams %s to pull request number %s" % (reviewers, team_reviewers, number))

    # Adding users
    data = {"reviewers": reviewers}
    REVIEWERS_URL = "%s/%s/requested_reviewers" % (PULLS_URL, number)
    response = requests.post(REVIEWERS_URL, json=data, headers=HEADERS)
    if response.status_code != 201:
        abort_if_fail(response, "Unable to add reviewers")

    # Adding teams
    if team_reviewers:
        data = {"team_reviewers": team_reviewers}
        TEAM_REVIEWERS_URL = "%s/%s/requested_teams" % (PULLS_URL, number)
        response = requests.post(TEAM_REVIEWERS_URL, json=data, headers=HEADERS)
        if response.status_code != 201:
            abort_if_fail(response, "Unable to add team reviewers")

    print("Added reviewers and teams to the pull request!")


def main():
    """Main entry point for script."""
    # 1. Get envars and initialize constants
    GITHUB_TOKEN = get_envar("GITHUB_TOKEN")
    GITHUB_REPOSITORY = get_envar("GITHUB_REPOSITORY")
    PULL_REQUEST_TITLE = get_envar("PULL_REQUEST_TITLE")
    PULL_REQUEST_BODY = get_envar("PULL_REQUEST_BODY")
    PULL_REQUEST_TARGET = get_envar("PULL_REQUEST_TARGET")
    PULL_REQUEST_SOURCE = get_envar("PULL_REQUEST_SOURCE")
    PULL_REQUEST_DRAFT = os.environ.get("PULL_REQUEST_DRAFT", "").lower() in ["true", "1", "yes"]
    PULL_REQUEST_UPDATE = os.environ.get("PULL_REQUEST_UPDATE")
    ASSIGNEES = os.environ.get("ASSIGNEES")
    REVIEWERS = os.environ.get("REVIEWERS")
    TEAM_REVIEWERS = os.environ.get("TEAM_REVIEWERS")

    # Set headers for GitHub API requests
    global HEADERS
    HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}

    # Set the repo URL and pulls URL for the requests
    global REPO_URL, PULLS_URL, ISSUE_URL
    REPO_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
    PULLS_URL = f"{REPO_URL}/pulls"
    ISSUE_URL = f"{REPO_URL}/issues"

    # Check events JSON
    check_events_json()

    # 2. Check for an existing PR
    existing_pr = list_pull_requests(PULL_REQUEST_TARGET, PULL_REQUEST_SOURCE)
    entry = find_pull_request(existing_pr, PULL_REQUEST_SOURCE)

    if entry:
        # If the pull request already exists and we want to update it
        if PULL_REQUEST_UPDATE:
            response = update_pull_request(entry, PULL_REQUEST_TITLE, PULL_REQUEST_BODY, PULL_REQUEST_TARGET)
            set_pull_request_groups(response)
            print("Updated existing pull request.")
            if ASSIGNEES:
                add_assignees(entry, ASSIGNEES)
            if REVIEWERS or TEAM_REVIEWERS:
                add_reviewers(entry, REVIEWERS, TEAM_REVIEWERS)
            return

    # 3. Create a new pull request if not updating
    response = open_pull_request(PULL_REQUEST_TITLE, PULL_REQUEST_BODY, PULL_REQUEST_TARGET, PULL_REQUEST_SOURCE, PULL_REQUEST_DRAFT)
    set_pull_request_groups(response)

    # 4. Add assignees and reviewers to the newly created pull request
    if ASSIGNEES:
        add_assignees(response.json(), ASSIGNEES)
    if REVIEWERS or TEAM_REVIEWERS:
        add_reviewers(response.json(), REVIEWERS, TEAM_REVIEWERS)


if __name__ == "__main__":
    main()

import os
import sys
import requests
import json
import re
from flask import Flask
from flask import request

app = Flask(__name__)

USERNAME = 'gitlab'
ICON_URL = 'https://gitlab.com/uploads/project/avatar/13083/gitlab-logo-square.png'
MATTERMOST_WEBHOOK_URL = '' # Paste the Mattermost webhook URL you created here
CHANNEL = '' # Leave this blank to post to the default channel of your webhook
ROUTING = []
MATTERMOST_TOKEN = ''

PUSH_EVENT = 'push'
ISSUE_EVENT = 'issue'
TAG_EVENT = 'tag_push'
COMMENT_EVENT = 'note'
MERGE_EVENT = 'merge_request'

@app.route('/')
def root():
    """
    Home handler
    """

    return "OK"

@app.route('/new_post', methods=['POST'])
def new_post():
    """
    Mattermost new post event handler
    """

    data = request.form

    if data['token'] != MATTERMOST_TOKEN:
        print 'Tokens did not match, it is possible that this request came from somewhere other than Mattermost'
        return 'OK'

    translate_text = data['text'][len(data['trigger_word']):]

    if len(translate_text) == 0:
        print "No command provided, abort"
        return 'OK'

    parse = translate_text.split(' ')

    if parse[0] == 'help':
        text = text_help()
    else:
        text = ':-( Unknown command\n'

    resp_data = {}
    resp_data['text'] = text
    resp_data['username'] = USERNAME
    resp_data['icon_url'] = ICON_URL

    resp = Response(content_type='application/json')
    resp.set_data(json.dumps(resp_data))

    return resp

@app.route('/new_event', methods=['POST'])
def new_event():
    """
    GitLab event handler, handles POST events from a GitLab project
    """

    if request.json is None:
        print 'Invalid Content-Type'
        return 'Content-Type must be application/json and the request body must contain valid JSON', 400

    data = request.json
    object_kind = data['object_kind']

    text = ''
    base_url = ''

    if object_kind == PUSH_EVENT:
        text = '%s pushed %d commit(s) into the `%s` branch for project [%s](%s).' % (
            data['user_name'],
            data['total_commits_count'],
            data['ref'],
            data['repository']['name'],
            data['repository']['homepage']
        )
    elif object_kind == ISSUE_EVENT:
        action = data['object_attributes']['action']

        if action == 'open' or action == 'reopen':
            description = add_markdown_quotes(data['object_attributes']['description'])

            text = '##### [%s](%s)\n*[Issue #%s](%s/issues) created by %s in [%s](%s) on [%s](%s)*\n %s' % (
                data['object_attributes']['title'],
                data['object_attributes']['url'],
                data['object_attributes']['iid'],
                data['repository']['homepage'],
                data['user']['username'],
                data['repository']['name'],
                data['repository']['homepage'],
                data['object_attributes']['created_at'],
                data['object_attributes']['url'],
                description
            )

            base_url = data['repository']['homepage']
    elif object_kind == TAG_EVENT:
        text = '%s pushed tag `%s` to the project [%s](%s).' % (
            data['user_name'],
            data['ref'],
            data['repository']['name'],
            data['repository']['homepage']
        )
    elif object_kind == COMMENT_EVENT:
        symbol = ''
        type_grammar = 'a'
        note_type = data['object_attributes']['noteable_type'].lower()
        note_id = ''
        parent_title = ''

        if note_type == 'mergerequest':
            symbol = '!'
            note_id = data['merge_request']['iid']
            parent_title = data['merge_request']['title']
            note_type = 'merge request'
        elif note_type == 'snippet':
            symbol = '$'
            note_id = data['snippet']['iid']
            parent_title = data['snippet']['title']
        elif note_type == 'issue':
            symbol = '#'
            note_id = data['issue']['iid']
            parent_title = data['issue']['title']
            type_grammar = 'an'

        subtitle = ''
        if note_type == 'commit':
            subtitle = '%s' % data['commit']['id']
        else:
            subtitle = '%s%s - %s' % (symbol, note_id, parent_title)

        description = add_markdown_quotes(data['object_attributes']['note'])

        text = '##### **New Comment** on [%s](%s)\n*[%s](https://gitlab.com/u/%s) commented on %s %s in [%s](%s) on [%s](%s)*\n %s' % (
            subtitle,
            data['object_attributes']['url'],
            data['user']['username'],
            data['user']['username'],
            type_grammar,
            note_type,
            data['repository']['name'],
            data['repository']['homepage'],
            data['object_attributes']['created_at'],
            data['object_attributes']['url'],
            description
        )

        base_url = data['repository']['homepage']
    elif object_kind == MERGE_EVENT:
        action = data['object_attributes']['action']

        if action == 'open':
            text_action = 'created a'
        elif action == 'reopen':
            text_action = 'reopened a'
        elif action == 'update':
            text_action = 'updated a'
        elif action == 'merge':
            text_action = 'accepted a'
        elif action == 'close':
            text_action = 'closed a'

        text = '##### [!%s - %s](%s)\n*[%s](https://gitlab.com/u/%s) %s merge request in [%s](%s) on [%s](%s)*' % (
            data['object_attributes']['iid'],
            data['object_attributes']['title'],
            data['object_attributes']['url'],
            data['user']['username'],
            data['user']['username'],
            text_action,
            data['object_attributes']['target']['name'],
            data['object_attributes']['target']['web_url'],
            data['object_attributes']['created_at'],
            data['object_attributes']['url']
        )

        if action == 'open':
            description = add_markdown_quotes(data['object_attributes']['description'])
            text = '%s\n %s' % (
                text,
                description
            )

        base_url = data['object_attributes']['target']['web_url']

    if len(text) == 0:
        print 'Text was empty so nothing sent to Mattermost, object_kind=%s' % object_kind
        return 'OK'

    if len(base_url) != 0:
        text = fix_gitlab_links(base_url, text)

    # Route to channel, if configured
    channel = ''
    if ROUTING:
        if object_kind == MERGE_EVENT:
            repo = data['object_attributes']['target']['name']
        else:
            repo = data['repository']['name']
        if repo in ROUTING.keys():
            channel = ROUTING[repo]

    post_text(text, channel)

    return 'OK'

def post_text(text, channel):
    """
    Mattermost POST method, posts text to the Mattermost incoming webhook URL
    """

    data = {}
    data['text'] = text
    if len(USERNAME) > 0:
        data['username'] = USERNAME
    if len(ICON_URL) > 0:
        data['icon_url'] = ICON_URL
    if len(channel) == 0:
        # Use default for Webhook
        data['channel'] = CHANNEL
    else:
        data['channel'] = channel

    headers = {'Content-Type': 'application/json'}
    r = requests.post(MATTERMOST_WEBHOOK_URL, headers=headers, data=json.dumps(data))

    if r.status_code is not requests.codes.ok:
        print 'Encountered error posting to Mattermost URL %s, status=%d, response_body=%s' % (MATTERMOST_WEBHOOK_URL, r.status_code, r.json())

def fix_gitlab_links(base_url, text):
    """
    Fixes gitlab upload links that are relative and makes them absolute
    """

    matches = re.findall('(\[[^]]*\]\s*\((/[^)]+)\))', text)

    for (replace_string, link) in matches:
        new_string = replace_string.replace(link, base_url + link)
        text = text.replace(replace_string, new_string)

    return text

def add_markdown_quotes(text):
    """
    Add Markdown quotes around a piece of text
    """

    if len(text) == 0:
        return ''

    split_desc = text.split('\n')

    for index, line in enumerate(split_desc):
        split_desc[index] = '> ' + line

    return '\n'.join(split_desc)

def text_help():
    return "```Commands available:", \
            "\thelp: Show this help", \
            "```"

if __name__ == "__main__":
    # Read configuration from JSON
    if not os.path.exists('config.json'):
        print 'config.json missing. Please see instructions in README.md'
        sys.exit()

    try:
        config = json.load(open('config.json'))
    except:
        print "config.json is malformed"
        sys.exit()

    if 'port' not in config.keys() \
            or not isinstance(config['port'], int) \
            or config['port'] not in range(0, 65536):
        print 'Missing or malformed port. Must be an integer [0..65535]'
        sys.exit()
    port = config['port']
    # If some keys are missing in config, use default values defined above
    if 'username' in config.keys():
        USERNAME = config['username']
    if 'icon_url' in config.keys():
        ICON_URL = config['icon_url']
    if 'channel_name' in config.keys():
        CHANNEL = config['channel_name']

    if 'routing' in config.keys():
        ROUTING = config['routing']

    if 'token' in config.keys():
        MATTERMOST_TOKEN = config['token']

    if 'webhook_url' in config.keys() and len(config['webhook_url']) == 0:
        print 'Missing Mattermost Webhook Url. Please see instructions in README.md'
        sys.exit()
    else:
        MATTERMOST_WEBHOOK_URL = config['webhook_url']


    app.run(host='0.0.0.0', port=port)

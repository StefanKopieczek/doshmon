import logging
import json
import os
import re
import requests
from datetime import datetime
from uuid import uuid4


logger = logging.getLogger(__name__)


MONTHLY_BUDGET = 500


class Doshmon:
    def __init__(self, todoist, project_id):
        self.api = todoist
        self.project_id = project_id

    def do_housekeeping(self):
        logger.info('Starting housekeeping...')
        _projects, sections, tasks = self.api.get_state(project_id=self.project_id)
        cmds = []
        cmds.extend(self.add_missing_sections(sections))
        cmds.extend(self.archive_unwanted_sections(sections, tasks))
        cmds.extend(self.set_section_order(sections))
        cmds.extend(self.set_section_titles(sections, tasks))
        self.api.do_update(cmds)

    def add_missing_sections(self, sections):
        logger.info('Adding missing sections...')
        expected = self._get_expected_sections()
        cmds = []
        for expected_section in expected:
            if not any(section['name'].lower().startswith(expected_section.lower()) for section in sections):
                name = f'{expected_section} (£0.00 / £{MONTHLY_BUDGET})'
                temp_id = random_uuid()
                cmds.append(self.api.add_section(name=name, project_id=self.project_id, temp_id=random_uuid()))
                sections.append({'id': temp_id, 'name': name, 'project_id': self.project_id})
                logger.info(f'Adding missing section {name}')
        logger.info(f'({len(cmds)} missing sections to add)')
        return cmds

    def archive_unwanted_sections(self, sections, tasks):
        logger.info('Archiving unwanted sections...')
        expected = self._get_expected_sections()
        cmds = []
        current_section_id = self._get_current_section_id(sections)
        for section in sections:
            if not any(section['name'].lower().startswith(e.lower()) for e in expected):
                tasks_to_move = [t for t in tasks if t['section_id'] == section['id'] and not t['checked'] and not t['is_deleted']]
                logger.info(f'Archiving unwanted section {section.name} (with {len(tasks_to_move)} tasks)')
                for task in tasks_to_move:
                    cmds.append(self.move_task_to_section(task['id'], current_section_id))
                    task['section_id'] == current_section_id
                cmds.append(self.api.archive_section(section['id']))
        logger.info(f'({len(cmds)} unwanted sections to archive)')
        return cmds

    def set_section_order(self, sections):
        logger.info('Ensuring section order...')
        section_order_map = [{"id": s['id'], "section_order": idx + 1} for idx, s in enumerate(sections)]
        return [self.api.reorder_sections(section_order_map)]

    def set_section_titles(self, sections, tasks):
        logger.info('Checking section titles...')
        cmds = []
        for section in sections:
            if section['name'].startswith('Backlog'):
                expected_title = 'Backlog'
            else:
                tasks_for_section = [t for t in tasks if t['section_id'] == section['id']]
                cost = self._get_total_cost(tasks_for_section)
                expected_title = f'{" ".join(section["name"].split()[:2])} (£{cost} / £{MONTHLY_BUDGET})'
                if section['id'] == self._get_current_section_id(sections) and cost > MONTHLY_BUDGET:
                    expected_title = expected_title.replace('(', '!!! ').replace(')', ' !!!')
            if expected_title != section['name']:
                cmds.append(self.api.rename_section(section['id'], expected_title))
                logger.info(f'Renaming section "{section["name"]}" to "{expected_title}"')
        logger.info(f'({len(cmds)} sections to rename)')
        return cmds

    def _get_expected_sections(self):
        expected_dts = []
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        for month in range(1, 13):
            if month == current_month:
                year = current_year
            elif month < current_month:
                year = current_year + 1
            else:
                year = current_year
            expected_dts.append(datetime(year=year, month=month, day=1))

        expected_dts.sort()
        expected = [dt.strftime('%B %Y') for dt in expected_dts]
        expected = expected[0:1] + ['Backlog'] + expected[1:]

        return expected

    def _get_total_cost(self, tasks):
        total = 0
        for t in tasks:
            trimmed_desc = re.sub(r'[^a-zA-Z0-9.£ ]+', '', t['content'])
            for w in trimmed_desc.split():
                if w.startswith('£'):
                    total += float(w[1:])
                    break
        return total

    def _get_current_section_id(self, sections):
        now = datetime.now()
        for s in sections:
            if s['name'].lower().startswith(f'{now.strftime("%B %Y")}'.lower()):
                return s['id']
        return None


class Todoist:
    def __init__(self, api_token):
        self.api_token = api_token

    def get_state(self, project_id=None):
        headers = {'Authorization': f'Bearer {self.api_token}'}
        data = {'sync_token': '*', 'resource_types': '["projects", "sections", "items"]'}
        response = requests.post('https://api.todoist.com/sync/v9/sync', headers=headers, data=data)
        response.raise_for_status()
        response = response.json()

        projects, sections, items = response['projects'], response['sections'], response['items']
        for section in sections:
            completed_items_r = requests.get('https://api.todoist.com/sync/v9/archive/items?section_id=' + section['id'], headers=headers)
            completed_items_r.raise_for_status()
            items.extend(completed_items_r.json()['items'])

        if project_id is not None:
            projects = [p for p in projects if p['id'] == project_id]
            sections = [s for s in sections if s['project_id'] == project_id]
            items = [i for i in items if i['project_id'] == project_id]

        return projects, sections, items

    @staticmethod
    def _command(f):
        def wrapped(*args, **kwargs):
            cmd = f(*args, **kwargs)
            logger.info(f'Queued command {cmd["uuid"]} ({cmd["type"]}) with args {cmd["args"]}')
            return cmd
        return wrapped

    @_command
    def add_section(self, name, project_id, temp_id):
        return {
            'type': 'section_add',
            'temp_id': temp_id,
            'uuid': random_uuid(),
            'args': {'name': name, 'project_id': project_id}
        }

    @_command
    def move_task_to_section(self, task_id, section_id):
        return {
            'type': 'item_move',
            'uuid': random_uuid(),
            'args': {
                'id': task_id,
                'section_id': section_id,
            }
        }

    @_command
    def reorder_sections(self, order_map):
        return {
            'type': 'section_reorder',
            'uuid': random_uuid(),
            'args': {'sections': order_map}
        }

    @_command
    def rename_section(self, section_id, name):
        return {
            'type': 'section_update',
            'uuid': random_uuid(),
            'args': {'id': section_id, 'name': name}
        }

    def do_update(self, commands):
        logger.debug(f'Running update with {len(commands)} commands')
        headers = {'Authorization': f'Bearer {self.api_token}'}
        data = {'commands': json.dumps(commands)}
        r = requests.post('https://api.todoist.com/sync/v9/sync', headers=headers, data=data)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as err:
            print('HTTP Error:')
            print(err.request.url)
            print(err)
            print(err.response.text)
            raise err
        logger.info(f'Update complete. Result: {r.text}')


def random_uuid():
    return str(uuid4())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s')
    logger.info('Doshmon starting...')
    api_token = os.environ.get('TODOIST_TOKEN')
    assert api_token, 'TODOIST_TOKEN variable not set'

    project_id = os.environ.get('PROJECT_ID')
    assert project_id, 'PROJECT_ID variable not set'

    todoist = Todoist(api_token)
    doshmon = Doshmon(todoist, project_id)
    doshmon.do_housekeeping()

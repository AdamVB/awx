from collections import OrderedDict
import json
import mock
import os
import sys

import pytest

# ansible uses `ANSIBLE_CALLBACK_PLUGINS` and `ANSIBLE_STDOUT_CALLBACK` to
# discover callback plugins;  `ANSIBLE_CALLBACK_PLUGINS` is a list of paths to
# search for a plugin implementation (which should be named `CallbackModule`)
#
# this code modifies the Python path to make our
# `awx.lib.tower_display_callback` callback importable (because `awx.lib`
# itself is not a package)
#
# we use the `tower_display_callback` imports below within this file, but
# Ansible also uses them when it discovers this file in
# `ANSIBLE_CALLBACK_PLUGINS`
CALLBACK = os.path.splitext(os.path.basename(__file__))[0]
PLUGINS = os.path.dirname(__file__)
with mock.patch.dict(os.environ, {'ANSIBLE_STDOUT_CALLBACK': CALLBACK,
                                  'ANSIBLE_CALLBACK_PLUGINS': PLUGINS}):
    from ansible.cli.playbook import PlaybookCLI
    from ansible.executor.playbook_executor import PlaybookExecutor
    from ansible.inventory import Inventory
    from ansible.parsing.dataloader import DataLoader
    from ansible.vars import VariableManager

    # Add awx/lib to sys.path so we can use the plugin
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if path not in sys.path:
        sys.path.insert(0, path)

    from tower_display_callback import TowerDefaultCallbackModule as CallbackModule  # noqa
    from tower_display_callback.events import event_context  # noqa


@pytest.fixture()
def local_cache():
    class Cache(OrderedDict):
        def set(self, key, value):
            self[key] = value
    return Cache()


@pytest.fixture()
def executor(tmpdir_factory, request):
    playbooks = request.node.callspec.params.get('playbook')
    playbook_files = []
    for name, playbook in playbooks.items():
        filename = str(tmpdir_factory.mktemp('data').join(name))
        with open(filename, 'w') as f:
            f.write(playbook)
        playbook_files.append(filename)

    cli = PlaybookCLI(['', 'playbook.yml'])
    cli.parse()
    options = cli.parser.parse_args([])[0]
    loader = DataLoader()
    variable_manager = VariableManager()
    inventory = Inventory(loader=loader, variable_manager=variable_manager,
                          host_list=['localhost'])
    variable_manager.set_inventory(inventory)

    return PlaybookExecutor(playbooks=playbook_files, inventory=inventory,
                            variable_manager=variable_manager, loader=loader,
                            options=options, passwords={})


@pytest.mark.parametrize('event', {'playbook_on_start',
                                   'playbook_on_play_start',
                                   'playbook_on_task_start', 'runner_on_ok',
                                   'playbook_on_stats'})
@pytest.mark.parametrize('playbook', [
{'helloworld.yml': '''
- name: Hello World Sample
  connection: local
  hosts: all
  gather_facts: no
  tasks:
    - name: Hello Message
      debug:
        msg: "Hello World!"
'''}  # noqa
])
def test_callback_plugin_receives_events(executor, local_cache, event,
                                         playbook):
    with mock.patch.object(event_context, 'cache', local_cache):
        executor.run()
        assert event in [task['event'] for task in local_cache.values()]


@pytest.mark.parametrize('playbook', [
{'no_log_on_ok.yml': '''
- name: args should not be logged when task-level no_log is set
  connection: local
  hosts: all
  gather_facts: no
  tasks:
    - shell: echo "SENSITIVE"
      no_log: true
'''},  # noqa
{'no_log_on_fail.yml': '''
- name: failed args should not be logged when task-level no_log is set
  connection: local
  hosts: all
  gather_facts: no
  tasks:
    - shell: echo "SENSITIVE"
      no_log: true
      failed_when: true
      ignore_errors: true
'''},  # noqa
{'no_log_on_skip.yml': '''
- name: skipped task args should be suppressed with no_log
  connection: local
  hosts: all
  gather_facts: no
  tasks:
    - shell: echo "SENSITIVE"
      no_log: true
      when: false
'''},  # noqa
{'no_log_on_play.yml': '''
- name: play-level no_log set
  connection: local
  hosts: all
  gather_facts: no
  no_log: true
  tasks:
      - name: args should not be logged when play-level no_log set
        shell: echo "SENSITIVE"
'''},  # noqa
])
def test_callback_plugin_no_log_filters(executor, local_cache, playbook):
    with mock.patch.object(event_context, 'cache', local_cache):
        executor.run()
        assert 'SENSITIVE' not in json.dumps(local_cache.items())


@pytest.mark.parametrize('playbook', [
{'strip_env_vars.yml': '''
- name: sensitive environment variables should be stripped from events
  connection: local
  hosts: all
  tasks:
    - shell: echo "Hello, World!"
'''},  # noqa
])
def test_callback_plugin_strips_task_environ_variables(executor, local_cache,
                                                       playbook):
    with mock.patch.object(event_context, 'cache', local_cache):
        executor.run()
        for event in local_cache.values():
            if event['event_data'].get('task') == 'setup':
                assert os.environ['VIRTUAL_ENV'] not in json.dumps(event)

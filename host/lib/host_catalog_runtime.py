"""Build the selected custom map's local adapters from the verified host inventory."""
import json

from community_maps import BY_ID, lua_map_definitions


def runtime_paths(root, run, definition):
    definitions = lua_map_definitions(root, run, definition)
    objectives = root / 'lua/objectives.lua'
    if definition.id in BY_ID:
        return definitions, objectives
    key = json.dumps(definition.id)
    generated_objectives = run / 'catalog-objectives.lua'
    with generated_objectives.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(f'local factories=assert(loadfile({json.dumps(objectives.as_posix(), ensure_ascii=False)}))()\n'
                     'local authored=assert(factories.Drydock,"authored_ctf_factory_missing")\n'
                     f'assert(authored==factories.Katabatic and factories[{key}]==nil,"invalid_authored_ctf_factory")\n'
                     f'factories[{key}]=authored\nreturn factories\n')
    return definitions, generated_objectives

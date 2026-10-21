"""Render harness-specific role files from one reviewed manifest.

Rendering is not dispatch or permission. Existing files must match or installation
refuses; replacing retired definitions requires a separately reviewed migration.
No model, permission-mode, MCP or security-setting override is generated.
Claude read-only adapters omit shell/write tools. Codex custom roles inherit the
parent sandbox: read_only is instruction-only unless that parent is restricted.
Reviewed upgrades must preserve old manifest/file hashes and backups before
removing only verified generated files; v1 deliberately has no overwrite flag.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('non-finite number')))


def validate(manifest):
    if type(manifest) is not dict or set(manifest) != {'schema', 'common_instructions', 'roles'}:
        raise ValueError('invalid manifest envelope')
    if manifest['schema'] != 'dimaggi-build-roles/v1':
        raise ValueError('unsupported manifest schema')
    common, roles = manifest['common_instructions'], manifest['roles']
    if not isinstance(common, str) or not common.strip() or len(common) > 30000:
        raise ValueError('bounded common instructions required')
    if type(roles) is not list or not 1 <= len(roles) <= 8:
        raise ValueError('one to eight reusable roles required')
    names = set()
    for role in roles:
        if type(role) is not dict or set(role) != {'name', 'description', 'instructions', 'mode'}:
            raise ValueError('invalid role fields')
        name = role['name']
        if not isinstance(name, str) or not re.fullmatch(r'dimaggi-[a-z][a-z0-9-]{1,48}', name) or name in names:
            raise ValueError('invalid or duplicate role name')
        names.add(name)
        if role['mode'] not in ('read_only', 'scoped_writer'):
            raise ValueError('unknown role mode')
        for field, limit in [('description', 500), ('instructions', 15000)]:
            if not isinstance(role[field], str) or not role[field].strip() or len(role[field]) > limit:
                raise ValueError('invalid role ' + field)
    return manifest


def render(manifest):
    validate(manifest)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    output = {}
    for role in manifest['roles']:
        instructions = manifest['common_instructions'] + '\n\n' + role['instructions']
        if role['mode'] == 'read_only':
            instructions += '\nRead-only role: return findings; do not edit source or publish changes.'
        instructions += '\nRole manifest SHA-256: ' + digest
        # JSON basic strings are valid TOML basic strings for these ordinary Unicode fields.
        quote = lambda v: json.dumps(v, ensure_ascii=False).replace(chr(127), r'\u007f')
        name = role['name']
        output[f'.codex/agents/{name}.toml'] = '\n'.join([
            '# Generated from the reviewed shared role manifest; do not edit independently.',
            'name = ' + quote(name), 'description = ' + quote(role['description']),
            'developer_instructions = ' + quote(instructions), ''])
        tools = 'Read, Glob, Grep, WebFetch, WebSearch' if role['mode'] == 'read_only' else 'Read, Glob, Grep, Write, Edit, Bash, WebFetch, WebSearch'
        output[f'.claude/agents/{name}.md'] = '\n'.join([
            '---', 'name: ' + name, 'description: ' + quote(role['description']),
            'tools: ' + tools, 'model: inherit', '---',
            '<!-- Generated from the reviewed shared role manifest. -->', instructions, ''])
    return output


def install(manifest, destination, *, check=False):
    """Preflight all paths before writes. Never replace different existing content.

This is a cooperative installer, not an adversarial filesystem sandbox. A trusted
operator must own the destination and exclude concurrent directory mutations.
    """
    root = Path(destination).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('destination must be an existing directory')
    rendered = render(manifest)
    for rel, content in rendered.items():
        target = root / rel
        for path in [target, *list(target.parents)[:3]]:
            if path.is_symlink():
                raise ValueError('symlinked adapter destination refused')
        for ancestor in list(target.parents)[:3]:
            if ancestor.exists() and not ancestor.is_dir():
                raise ValueError('adapter parent must be a directory')
        if not target.resolve().is_relative_to(root):
            raise ValueError('adapter path escapes destination')
        if target.exists() and (not target.is_file() or target.read_text() != content):
            raise ValueError('existing adapter differs: ' + rel)
    if not check:
        for rel, content in rendered.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                with target.open('x') as handle:
                    handle.write(content)
    return sorted(rendered)


def verify_installed(manifest, destination):
    paths = install(manifest, destination, check=True)
    missing = [p for p in paths if not (Path(destination) / p).is_file()]
    if missing:
        raise ValueError('missing installed adapters: ' + ', '.join(missing))
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest')
    p.add_argument('--destination', required=True)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    try:
        manifest = load(args.manifest)
        paths = verify_installed(manifest, args.destination) if args.check else install(manifest, args.destination)
    except (ValueError, OSError) as error:
        p.exit(2, str(error) + '\n')
    print(json.dumps({'mode': 'check' if args.check else 'installed', 'paths': paths,
                      'permission_changes': False, 'running_agents_created': False}, indent=2))


if __name__ == '__main__':
    main()

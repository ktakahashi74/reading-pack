"""Explicit contract identity; absent legacy fields never select a new policy."""
from __future__ import annotations

import copy

from reading_pack.errors import ReadingPackError


LEGACY_CONTRACT_VERSION = 'legacy-reader-evaluation-1'
ARTIFACT_CONTRACT_VERSION = 'artifact-acceptance-1'
SUPPORTED_CONTRACT_VERSIONS = (LEGACY_CONTRACT_VERSION, ARTIFACT_CONTRACT_VERSION)


def recipe_contract(recipe: dict) -> str:
    value = recipe.get('contract_version', LEGACY_CONTRACT_VERSION)
    if value not in SUPPORTED_CONTRACT_VERSIONS:
        raise ReadingPackError('unknown pipeline contract_version')
    return value


def manifest_contract(manifest: dict) -> str:
    selected = recipe_contract(manifest['recipe'])
    frozen = manifest.get('contract_version', LEGACY_CONTRACT_VERSION)
    if frozen not in SUPPORTED_CONTRACT_VERSIONS:
        raise ReadingPackError('unknown frozen pipeline contract_version')
    if selected != frozen:
        raise ReadingPackError('recipe and frozen pipeline contract_version differ')
    return frozen


def restart_contract_recipe(manifest: dict, recipe: dict) -> dict:
    """An omitted choice inherits the old policy; restart never migrates it."""
    frozen = manifest_contract(manifest)
    if 'contract_version' in recipe and recipe_contract(recipe) != frozen:
        raise ReadingPackError('restart cannot change contract_version; use a separate reassessment')
    result = copy.deepcopy(recipe)
    if frozen == ARTIFACT_CONTRACT_VERSION and 'contract_version' not in result:
        result['contract_version'] = frozen
    return result


def require_executable_contract(manifest: dict) -> None:
    manifest_contract(manifest)

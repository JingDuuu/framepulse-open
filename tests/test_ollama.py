import json

from fastapi.testclient import TestClient

from framepulse.config import LOCAL_OLLAMA, Settings, discover_models


def test_discovery_uses_capabilities_and_excludes_cloud_alias(monkeypatch):
    def respond(url, **kwargs):
        if url.endswith('/tags'):
            return {'models': [dict(name=name, modified_at=date) for name, date in
                    [('future-vision:small', '2026-10-15'), ('older:9b', '2026-09-01'),
                     ('custom-alias', '2026-10-16'), ('new-text', '2026-10-17'), ('unknown', '2026-10-18')]]}
        name = kwargs['body']['model']
        if name == 'unknown':
            raise OSError('unavailable metadata')
        return {'capabilities': ['completion'] if name == 'new-text' else ['vision'],
                'remote_host': 'https://ollama.com' if name == 'custom-alias' else ''}
    monkeypatch.setattr('framepulse.config._json', respond)
    result = discover_models()
    assert result['suggested_model'] == 'future-vision:small'
    assert next(m for m in result['models'] if m['name'] == 'custom-alias')['remote']
    assert next(m for m in result['models'] if m['name'] == 'unknown')['vision'] is None


def test_auto_connect_retries_without_model_and_remembers_success(tmp_path, monkeypatch):
    config = Settings(tmp_path / 'settings.local.json', {'provider_url': LOCAL_OLLAMA, 'model': ''})
    result = {'models': [], 'errors': ['not running'], 'reachable': False}
    monkeypatch.setattr('framepulse.config.discover_models', lambda url: result)
    assert not config.connect_ollama(automatic=True)['connected']
    assert config.snapshot()['auto_connect']
    result['models'] = [{'name': 'new-vision:latest', 'vision': True, 'remote': False}]
    assert config.connect_ollama(automatic=True)['connected']
    assert config.snapshot()['vision']
    assert json.loads(config.path.read_text())['model'] == 'new-vision:latest'
    reloaded = Settings(config.path, {'provider_url': LOCAL_OLLAMA, 'model': ''})
    assert reloaded.connect_ollama(automatic=True)['skipped']


def test_auto_connect_preserves_explicit_ai_off_and_remote_settings(tmp_path, monkeypatch):
    config = Settings(tmp_path / 'settings.local.json', {'provider_url': LOCAL_OLLAMA, 'model': ''})
    config.update({'provider_url': 'https://example.test/v1', 'model': '', 'vision': False})
    def unexpected(*args):
        raise AssertionError('automatic discovery must not override a manual choice')
    monkeypatch.setattr('framepulse.config.discover_models', unexpected)
    assert config.connect_ollama(automatic=True)['skipped']
    assert config.snapshot()['provider_url'] == 'https://example.test/v1'
    assert not config.snapshot()['vision']


def test_connect_endpoint_requires_token_and_connects_actual_config(tmp_path, monkeypatch):
    from framepulse.server import create_app
    monkeypatch.setattr('framepulse.server.provider_defaults', lambda: {'provider_url': LOCAL_OLLAMA, 'model': ''})
    monkeypatch.setattr('framepulse.config.discover_models', lambda url: {
        'models': [{'name': 'future-vision', 'vision': True, 'remote': False}], 'errors': []})
    with TestClient(create_app(tmp_path, token='test-token')) as client:
        assert client.post('/api/ollama/connect', json={}).status_code == 403
        reply = client.post('/api/ollama/connect', json={'automatic': True}, headers={'x-framepulse-token': 'test-token'})
        assert reply.status_code == 200
        assert reply.json()['connected']
        assert client.get('/api/config').json()['model'] == 'future-vision'


def test_settings_write_failure_does_not_apply_unsaved_change(tmp_path, monkeypatch):
    import pytest
    config = Settings(tmp_path / 'settings.local.json', {'provider_url': LOCAL_OLLAMA, 'model': ''})
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('framepulse.config.write_document', fail)
    with pytest.raises(OSError):
        config.update({'model': 'unsaved'})
    assert config.snapshot()['model'] == ''

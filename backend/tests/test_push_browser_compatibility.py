"""Documented provider URL formats, not real-browser delivery certification."""
import pytest

from app.push_destination import validate_push_destination
from app.workers import web_push
from tests import test_push_destination

transport = test_push_destination.transport

PROVIDER_URLS = [
    'https://updates.push.services.mozilla.com/push/v1/test-token?opaque=a%2Fb',
    'https://updates.push.services.mozilla.com/wpush/v2/test-token?opaque=a%2Fb',
    'https://fcm.googleapis.com/fcm/send/test-token?opaque=a%2Fb',
    'https://fcm.googleapis.com/wp/test-token?opaque=a%2Fb',
    'https://web.push.apple.com/Qtest-token?opaque=a%2Fb',
    'https://regional.push.apple.com/Qtest-token?opaque=a%2Fb',
]


@pytest.mark.parametrize('endpoint', PROVIDER_URLS)
async def test_provider_path_and_query_survive_validation_and_transport(transport, endpoint):
    target, _response, network, delete = transport
    target['endpoint'] = endpoint
    assert validate_push_destination(endpoint) == endpoint
    assert await web_push.send_push(target, {'title': 'Compatibility test'}) == 'accepted'
    network.assert_called_once()
    assert network.call_args.args[0].url == endpoint
    assert web_push._vapid_claims(endpoint)['aud'] == endpoint.split('/', 3)[0] + '//' + endpoint.split('/', 3)[2]
    delete.assert_not_awaited()


@pytest.mark.parametrize('endpoint', [
    'https://push.apple.com.attacker.example/Qtest-token',
    'https://fcm.googleapis.com.attacker.example/wp/test-token',
    'https://updates.push.services.mozilla.com.attacker.example/wpush/v2/test-token',
])
async def test_exact_deceptive_provider_suffixes_never_reach_network(transport, endpoint):
    target, _response, network, delete = transport
    target['endpoint'] = endpoint
    with pytest.raises(ValueError):
        await web_push.send_push(target, {'title': 'Compatibility test'})
    network.assert_not_called()
    delete.assert_not_awaited()

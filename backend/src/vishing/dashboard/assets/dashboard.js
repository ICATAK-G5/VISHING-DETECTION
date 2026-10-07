const byId = (id) => document.getElementById(id);
let refreshTimer;
let lastEffectiveLan;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`);
  return result;
}

function shortId(value) { return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : 'Unknown device'; }
function formatTime(value) { return value ? new Date(value).toLocaleString() : 'Never connected'; }

async function refresh() {
  try {
    const state = await api('/api/v1/desktop/state');
    if (lastEffectiveLan !== undefined && lastEffectiveLan !== state.allow_lan) clearPairingCode();
    lastEffectiveLan = state.allow_lan;
    const restartRequired = state.allow_lan !== state.allow_lan_requested;
    if (restartRequired) clearPairingCode();
    byId('service-label').textContent = 'Desktop service is ready';
    document.querySelector('.service-pill').classList.remove('offline');
    byId('lan-toggle').checked = state.allow_lan_requested;
    byId('start-pairing').disabled = restartRequired;
    byId('start-pairing').textContent = restartRequired ? 'Restart service to pair' : 'Start pairing';
    byId('network-badge').textContent = state.allow_lan ? 'LAN enabled' : 'Loopback only';
    byId('network-badge').className = `badge ${state.allow_lan ? 'wait' : 'neutral'}`;
    byId('network-note').textContent = restartRequired
      ? 'Restart required before pairing: stop the running desktop service (Ctrl+C in its terminal), then run scripts/start_desktop.ps1 again. Wait for “LAN enabled” here, click Start pairing, and scan that new QR. Any older QR may point to 127.0.0.1 and will not work from a phone.'
      : state.allow_lan
        ? (state.lan_addresses.length
          ? 'LAN listener is active on the private addresses listed below. Pick the address on the same Wi-Fi as your phone before starting a fresh pairing code.'
          : 'LAN is enabled, but this desktop has no private IPv4 address. Connect it to Wi-Fi and restart the service.')
        : 'Network access is off. The QR code will use 127.0.0.1, which is only reachable from this computer. Enable local-network access and restart before pairing a phone.';
    const addressWrap = byId('address-picker-wrap');
    addressWrap.classList.toggle('hidden', !state.allow_lan || restartRequired || !state.lan_addresses.length);
    const picker = byId('lan-address-select');
    const previousAddress = picker.value;
    picker.innerHTML = state.lan_addresses.map((address) => `<option value="${escapeHtml(address)}">${escapeHtml(address)}</option>`).join('');
    if (state.lan_addresses.includes(previousAddress)) picker.value = previousAddress;
    byId('lan-addresses').textContent = `${state.allow_lan && state.lan_addresses.length ? `Available phone addresses: ${state.lan_addresses.join(', ')} · ` : ''}TLS fingerprint: ${state.certificate_fingerprint}`;
    const devices = state.devices;
    const connected = devices.filter((item) => item.connected).length;
    byId('phone-count').textContent = connected;
    byId('device-total').textContent = devices.length;
    byId('devices').innerHTML = devices.length ? devices.map((item) => `
      <div class="device-row">
        <div class="request-icon">▯</div>
        <div class="device-info"><strong>${escapeHtml(item.device_name)} · ${escapeHtml(shortId(item.device_id))}</strong><small>Paired ${formatTime(item.paired_at)} · Last seen ${formatTime(item.last_seen)}</small></div>
        <span class="row-status ${item.connected ? 'connected' : ''}">${item.revoked ? 'Revoked' : item.connected ? 'Connected' : 'Disconnected'}</span>
        ${item.revoked ? '' : `<div class="actions"><button class="revoke" data-revoke="${escapeHtml(item.device_id)}">Revoke</button></div>`}
      </div>`).join('') : '<div class="empty-row">No phones are paired yet.</div>';
    byId('requests').innerHTML = state.pairing_requests.map((item) => `
      <div class="request-row"><div class="request-icon">⌁</div><div class="device-info"><strong>${escapeHtml(item.device_name)}</strong><small>Device ${escapeHtml(shortId(item.device_id))} · Request received ${formatTime(item.created_at)}</small></div><div class="actions"><button class="approve" data-approve="${escapeHtml(item.request_id)}">Approve</button><button class="reject" data-reject="${escapeHtml(item.request_id)}">Reject</button></div></div>`).join('');
    if (state.pairing_requests.length) {
      byId('pairing-badge').textContent = 'Approval needed';
      byId('pairing-badge').className = 'badge wait';
    } else if (state.pairing.active) {
      byId('pairing-badge').textContent = 'Waiting for phone';
      byId('pairing-badge').className = 'badge wait';
    } else if (state.pairing.used) {
      byId('pairing-badge').textContent = 'Code used · start a new one';
      byId('pairing-badge').className = 'badge neutral';
    } else if (state.pairing.expired) {
      byId('pairing-badge').textContent = 'Code expired';
      byId('pairing-badge').className = 'badge neutral';
    } else if (!byId('pairing-qr').src) byId('pairing-badge').textContent = 'Not started';
  } catch (error) {
    byId('service-label').textContent = 'Desktop service unavailable';
    document.querySelector('.service-pill').classList.add('offline');
  }
}

function clearPairingCode() {
  byId('pairing-qr').removeAttribute('src');
  byId('pairing-active').classList.add('hidden');
  byId('pairing-empty').classList.remove('hidden');
  byId('pairing-endpoint').textContent = '';
  byId('pairing-endpoint').classList.add('hidden');
}

function escapeHtml(text) { return String(text).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }

byId('start-pairing').addEventListener('click', async () => {
  try {
    const result = await api('/api/v1/desktop/pairing', {
      method: 'POST',
      body: JSON.stringify({ address: byId('lan-toggle').checked ? byId('lan-address-select').value : null })
    });
    byId('pairing-empty').classList.add('hidden');
    byId('pairing-active').classList.remove('hidden');
    byId('pairing-qr').src = result.qr_svg;
    byId('pairing-code').textContent = result.payload.pairing_code;
    byId('pairing-expiry').textContent = new Date(result.payload.expires_at).toLocaleTimeString();
    byId('pairing-endpoint').textContent = `This QR connects to ${new URL(result.payload.https_url).host}. On a phone, this must be the desktop’s Wi-Fi address, never 127.0.0.1.`;
    byId('pairing-endpoint').classList.remove('hidden');
    byId('pairing-badge').textContent = 'Waiting for phone';
    byId('pairing-badge').className = 'badge wait';
    await refresh();
  } catch (error) { alert(error.message); }
});

byId('lan-toggle').addEventListener('change', async (event) => {
  const enabled = event.target.checked;
  if (enabled && !confirm('Allow encrypted phone connections from devices on your private local network? The service must restart before this takes effect.')) {
    event.target.checked = false;
    return;
  }
  try {
    await api('/api/v1/desktop/network', { method: 'PUT', body: JSON.stringify({ allow_lan: enabled }) });
    await refresh();
    alert('Setting saved. Restart the desktop service to apply it. LAN access is limited to phone API routes; dashboard controls remain local to this computer.');
  } catch (error) { event.target.checked = !enabled; alert(error.message); }
});

document.addEventListener('click', async (event) => {
  const approve = event.target.dataset.approve;
  const reject = event.target.dataset.reject;
  const revoke = event.target.dataset.revoke;
  try {
    if (approve) await api(`/api/v1/desktop/pairing/${encodeURIComponent(approve)}/approve`, { method: 'POST', body: '{}' });
    if (reject) await api(`/api/v1/desktop/pairing/${encodeURIComponent(reject)}/reject`, { method: 'POST', body: '{}' });
    if (revoke && confirm('Revoke this phone? It will need to pair again before reconnecting.')) await api(`/api/v1/desktop/devices/${encodeURIComponent(revoke)}`, { method: 'DELETE' });
    await refresh();
  } catch (error) { alert(error.message); }
});

refresh();
refreshTimer = setInterval(refresh, 1800);

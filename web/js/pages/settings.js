/* Settings: Hugging Face token, autostart, retention, paths, stats reset. */
import { html, E, useState, useEffect, api, useApp, useFetch } from '../lib.js';

export function SettingsPage() {
  const { toast, fail, status } = useApp();
  const settings = useFetch('/api/settings');
  const presets = useFetch('/api/presets');
  const [token, setToken] = useState('');
  const [savingToken, setSavingToken] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const s = settings.data;

  const save = async (patch, msg) => {
    try { await api.put('/api/settings', patch); settings.reload(); if (msg) toast({ tone: 'success', title: msg }); } catch (e) { fail(e); }
  };
  const saveToken = async (e) => {
    e.preventDefault();
    setSavingToken(true);
    await save({ hf_token: token }, token ? 'Token saved' : 'Token removed');
    setToken('');
    setSavingToken(false);
  };
  const reset = async () => {
    try { const r = await api.post('/api/stats/reset', { confirm: true }); toast({ title: `Deleted ${r.deleted.toLocaleString('en-US')} request records` }); setResetOpen(false); } catch (e) { fail(e); }
  };
  if (!s) return html`<div className="page"><${E.Spinner} label="Loading settings" /></div>`;

  const tokenHint = {
    env: 'Using HF_TOKEN from the container environment.',
    settings: `Using the token saved here (${s.hf_token_hint}).`,
    hf_home: 'Using the token from your Hugging Face CLI login in HF_HOME.',
    '': 'No token. Public models download fine; gated and private ones need a token.',
  }[s.hf_token_source || ''];

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Settings</h1>
        <p className="text-body">Stored in the Kiln database under ${s.paths.data}.</p>
      </div>
    </header>

    <div className="grid grid--2">
      <${E.Card} title="Hugging Face" description="Read access is enough. Create a token at huggingface.co/settings/tokens.">
        <form className="stack" onSubmit=${saveToken}>
          <${E.TextField} label="Access token" type="password" mono autoComplete="off" value=${token} onChange=${(e) => setToken(e.target.value)}
            placeholder=${s.hf_token_set ? 'Saved. Enter a new one to replace it.' : 'hf_…'} hint=${tokenHint} />
          <div className="row">
            <${E.Button} type="submit" variant="primary" loading=${savingToken} disabled=${!token}>Save token<//>
            ${s.hf_token_set && html`<${E.Button} variant="ghost" onClick=${() => save({ hf_token: '' }, 'Token removed')}>Remove saved token<//>`}
          </div>
        </form>
      <//>

      <${E.Card} title="Startup and history">
        <div className="stack">
          <${E.Select} label="Start on boot" value=${s.autostart_preset_id ? String(s.autostart_preset_id) : ''}
            onChange=${(e) => save({ autostart_preset_id: e.target.value || null }, e.target.value ? 'Autostart preset set' : 'Autostart turned off')}
            options=${[{ value: '', label: 'Nothing, wait for me' }, ...((presets.data || []).map((p) => ({ value: String(p.id), label: p.name })))]}
            hint="Loaded when the container starts, so the API is ready after a reboot." />
          <${E.Select} label="Keep request history" value=${String(s.retention_days || 0)}
            onChange=${(e) => save({ retention_days: Number(e.target.value) }, 'Retention updated')}
            options=${[{ value: '0', label: 'Forever' }, { value: '365', label: '1 year' }, { value: '180', label: '6 months' }, { value: '90', label: '90 days' }, { value: '30', label: '30 days' }]}
            hint="Older request records and GPU samples are deleted automatically. Run history is always kept." />
        </div>
      <//>

      <${E.Card} title="Paths and ports">
        <dl className="facts facts--list">
          ${[['Models', s.paths.models], ['Downloads', s.paths.downloads], ['HF_HOME', s.paths.hf_home], ['Data', s.paths.data], ['llama-server', s.paths.llama_server]]
            .map(([k, v]) => html`<div key=${k}><dt className="text-caption muted">${k}</dt><dd className="text-code">${v}</dd></div>`)}
          <div><dt className="text-caption muted">Control UI</dt><dd className="text-code">:${s.ports.ui}</dd></div>
          <div><dt className="text-caption muted">API</dt><dd className="text-code">:${s.ports.api}/v1</dd></div>
        </dl>
      <//>

      <${E.Card} title="llama.cpp">
        <dl className="facts facts--tight">
          <div><dt className="text-caption muted">Version</dt><dd className="text-code">${status && status.llama ? status.llama.version || '–' : '–'}</dd></div>
          <div><dt className="text-caption muted">Devices</dt><dd className="text-body">${status && status.llama && status.llama.devices.length ? status.llama.devices.join(', ') : 'None detected'}</dd></div>
          <div><dt className="text-caption muted">GPU telemetry</dt><dd className="text-body">${status && status.gpu && status.gpu.available ? 'Reading amdgpu sysfs' : 'Unavailable'}</dd></div>
        </dl>
      <//>
    </div>

    <${E.Card} title="Reset statistics" description="Deletes every request record and GPU sample. Presets, models and run history stay.">
      <${E.Button} variant="secondary" icon="trash" onClick=${() => setResetOpen(true)}>Reset statistics<//>
    <//>
    <${E.Dialog} open=${resetOpen} onClose=${() => setResetOpen(false)} size="sm" title="Reset all statistics?"
      description="The dashboard starts over from zero. Export a CSV from the Requests page first if you want to keep a copy."
      footer=${html`<${E.Button} variant="secondary" onClick=${() => setResetOpen(false)}>Cancel<//><${E.Button} variant="danger" icon="trash" onClick=${reset}>Delete statistics<//>`} />
  </div>`;
}

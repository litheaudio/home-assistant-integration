/**
 * Lithe Audio Custom Card
 *
 * Branded media-player card for Lithe Audio speakers in Home Assistant.
 *
 * Features:
 *  - Now Playing display (title, artist, source, artwork)
 *  - Play/pause, skip, volume slider
 *  - Quick volume presets (0/20/40/60/80/100)
 *  - Favourite slot picker (1-9)
 *  - Source switcher dropdown
 *  - Heart button (save to next free favourite slot)
 *
 * Configuration:
 *   type: custom:lithe-audio-card
 *   entity: media_player.wifi_pro_2_3503b8
 *   name: "Kitchen Speaker"        # optional override
 *   show_artwork: true              # optional, default true
 *   show_quick_volumes: true        # optional, default true
 *   show_favourites: true           # optional, default true
 *   show_source: true               # optional, default true
 */

class LitheAudioCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('You must specify a media_player entity');
    }
    this._config = {
      show_artwork: true,
      show_quick_volumes: true,
      show_favourites: true,
      show_source: true,
      ...config,
    };
    this._render();
  }

  // Tell HA which entity to use as a sensible default when the card is
  // added from the picker. HA picks the first Lithe Audio media_player
  // entity it finds — no manual config required.
  static getStubConfig(hass, entities, entitiesFallback) {
    // Prefer a Lithe Audio media_player. Detect by looking for the
    // common attribute set this integration exposes (`product`, `firmware`,
    // `favourites`). Falls back to any media_player.
    if (hass && hass.states) {
      for (const eid of Object.keys(hass.states)) {
        if (!eid.startsWith('media_player.')) continue;
        const attrs = hass.states[eid].attributes || {};
        if ('favourites' in attrs && 'firmware' in attrs) {
          return { entity: eid };
        }
      }
      // Fallback: first media_player entity
      for (const eid of Object.keys(hass.states)) {
        if (eid.startsWith('media_player.')) return { entity: eid };
      }
    }
    return { entity: '' };
  }

  // Tell HA to use our custom editor (defined below) for the visual UI
  static getConfigElement() {
    return document.createElement('lithe-audio-card-editor');
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  static get LITHE_GREEN() { return '#28a76d'; }
  static get LITHE_DARK()  { return '#0f3c28'; }
  static get LITHE_BG()    { return '#0a0a0a'; }

  _render() {
    if (!this._hass || !this._config.entity) return;
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:16px;color:var(--error-color);">
            Entity ${this._config.entity} not found.
          </div>
        </ha-card>`;
      return;
    }

    const attrs = state.attributes || {};
    const name  = this._config.name || attrs.friendly_name || state.entity_id;
    const playing = state.state === 'playing';
    const paused  = state.state === 'paused';
    const idle    = !playing && !paused;
    const vol     = attrs.volume_level != null ? Math.round(attrs.volume_level * 100) : 0;
    const muted   = attrs.is_volume_muted;
    const title   = attrs.media_title || (idle ? 'Nothing playing' : '—');
    const artist  = attrs.media_artist || '';
    const src     = attrs.source_name || attrs.source || '';
    const artwork = attrs.entity_picture;
    const favs    = attrs.favourites || [];

    const sourceList = attrs.source_list || [];

    // Pick a contextually-correct play/pause icon
    const playIcon = playing ? 'mdi:pause' : 'mdi:play';

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          --lithe-green: ${LitheAudioCard.LITHE_GREEN};
          --lithe-dark:  ${LitheAudioCard.LITHE_DARK};
          --lithe-bg:    ${LitheAudioCard.LITHE_BG};
        }
        ha-card {
          padding: 0;
          overflow: hidden;
        }
        .lithe-card {
          background:
            linear-gradient(135deg, var(--lithe-dark) 0%, #000 100%);
          color: #fff;
          padding: 16px;
          border-radius: var(--ha-card-border-radius, 12px);
        }
        .header {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-bottom: 16px;
        }
        .artwork {
          width: 64px;
          height: 64px;
          border-radius: 8px;
          background: #1a1a1a no-repeat center/cover;
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--lithe-green);
        }
        .header-text {
          flex: 1;
          min-width: 0;
        }
        .name {
          font-size: 14px;
          font-weight: 600;
          color: var(--lithe-green);
          margin-bottom: 4px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .title {
          font-size: 16px;
          font-weight: 500;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .artist {
          font-size: 13px;
          opacity: 0.7;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .source-pill {
          display: inline-block;
          padding: 2px 8px;
          background: rgba(255,255,255,0.1);
          border-radius: 10px;
          font-size: 11px;
          margin-top: 4px;
          opacity: 0.8;
        }
        .controls {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          margin: 16px 0;
        }
        button.ctrl {
          background: rgba(255,255,255,0.08);
          border: 1px solid rgba(255,255,255,0.1);
          color: #fff;
          padding: 8px;
          border-radius: 50%;
          width: 40px;
          height: 40px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: background 0.15s;
        }
        button.ctrl:hover {
          background: rgba(255,255,255,0.15);
        }
        button.ctrl.play {
          width: 56px;
          height: 56px;
          background: var(--lithe-green);
          border-color: var(--lithe-green);
        }
        button.ctrl.play:hover {
          background: #2dbb7d;
        }
        button.ctrl.heart {
          color: #ff6b6b;
        }
        button.ctrl ha-icon {
          --mdc-icon-size: 22px;
        }
        button.ctrl.play ha-icon {
          --mdc-icon-size: 28px;
        }
        .volume-row {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 4px;
        }
        .vol-label {
          width: 36px;
          font-size: 12px;
          font-variant-numeric: tabular-nums;
          opacity: 0.7;
        }
        input[type="range"] {
          flex: 1;
          accent-color: var(--lithe-green);
        }
        .quick-vols {
          display: flex;
          gap: 4px;
          margin-top: 8px;
          flex-wrap: wrap;
        }
        button.qv {
          flex: 1;
          min-width: 44px;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.1);
          color: #fff;
          padding: 6px 4px;
          border-radius: 6px;
          font-size: 12px;
          cursor: pointer;
        }
        button.qv:hover {
          background: rgba(40,167,109,0.25);
          border-color: var(--lithe-green);
        }
        .section {
          margin-top: 14px;
          padding-top: 14px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .section-title {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          opacity: 0.6;
          margin-bottom: 8px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        button.save-fav-now {
          background: rgba(255,107,107,0.15);
          border: 1px solid rgba(255,107,107,0.4);
          color: #ff6b6b;
          padding: 4px 10px;
          border-radius: 6px;
          font-size: 10px;
          font-weight: 600;
          text-transform: none;
          letter-spacing: 0;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 3px;
          opacity: 1;
        }
        button.save-fav-now:hover {
          background: rgba(255,107,107,0.3);
        }
        button.save-fav-now ha-icon {
          --mdc-icon-size: 14px;
        }
        .fav-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 6px;
        }
        button.fav {
          background: linear-gradient(135deg,
            rgba(40,167,109,0.15) 0%,
            rgba(255,255,255,0.05) 100%);
          border: 1px solid rgba(40,167,109,0.35);
          color: #fff;
          padding: 12px 6px;
          border-radius: 8px;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          text-align: center;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          min-height: 44px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
          transition: all 0.15s;
        }
        button.fav:hover {
          background: linear-gradient(135deg,
            rgba(40,167,109,0.4) 0%,
            rgba(40,167,109,0.2) 100%);
          border-color: var(--lithe-green);
          transform: translateY(-1px);
        }
        button.fav:active {
          transform: translateY(0);
        }
        button.fav.empty {
          opacity: 0.35;
          border-style: dashed;
          font-size: 10px;
        }
        button.fav .fav-num {
          font-size: 9px;
          opacity: 0.7;
          margin-right: 4px;
        }
        select.src {
          width: 100%;
          background: rgba(255,255,255,0.08);
          border: 1px solid rgba(255,255,255,0.15);
          color: #fff;
          padding: 8px;
          border-radius: 6px;
          font-size: 13px;
        }
        select.src option {
          background: var(--lithe-dark);
          color: #fff;
        }
      </style>

      <ha-card>
        <div class="lithe-card">
          <div class="header">
            <div class="artwork" style="${artwork ? `background-image:url(${artwork})` : ''}">
              ${!artwork ? '<ha-icon icon="mdi:speaker-wireless"></ha-icon>' : ''}
            </div>
            <div class="header-text">
              <div class="name">${this._esc(name)}</div>
              <div class="title">${this._esc(title)}</div>
              ${artist ? `<div class="artist">${this._esc(artist)}</div>` : ''}
              ${src ? `<div class="source-pill">${this._esc(src)}</div>` : ''}
            </div>
          </div>

          <div class="controls">
            <button class="ctrl" data-action="prev" title="Previous">
              <ha-icon icon="mdi:skip-previous"></ha-icon>
            </button>
            <button class="ctrl play" data-action="play_pause" title="Play/Pause">
              <ha-icon icon="${playIcon}"></ha-icon>
            </button>
            <button class="ctrl" data-action="next" title="Next">
              <ha-icon icon="mdi:skip-next"></ha-icon>
            </button>
            <button class="ctrl heart" data-action="heart" title="Save to favourite">
              <ha-icon icon="mdi:heart"></ha-icon>
            </button>
          </div>

          <div class="volume-row">
            <button class="ctrl" data-action="mute" title="Mute" style="width:32px;height:32px;">
              <ha-icon icon="${muted ? 'mdi:volume-off' : 'mdi:volume-high'}"></ha-icon>
            </button>
            <input type="range" min="0" max="100" value="${vol}" data-action="volume" />
            <span class="vol-label">${vol}%</span>
          </div>

          ${this._config.show_quick_volumes ? `
          <div class="quick-vols">
            ${[0, 20, 40, 60, 80, 100].map(v =>
              `<button class="qv" data-vol="${v}">${v}%</button>`
            ).join('')}
          </div>
          ` : ''}

          ${this._config.show_favourites ? `
          <div class="section">
            <div class="section-title">
              <span>❤ Favourites</span>
              <button class="save-fav-now" data-action="heart" title="Save what's playing now">
                <ha-icon icon="mdi:plus-circle"></ha-icon> Save current
              </button>
            </div>
            <div class="fav-grid">
              ${[1,2,3,4,5,6,7,8,9].map(slot => {
                const fav = favs.find(f => f.slot === slot);
                if (fav && fav.name && fav.name !== '(empty)') {
                  return `<button class="fav" data-slot="${slot}" title="${this._esc(fav.name)}">
                    <span class="fav-num">${slot}</span>${this._esc(fav.name)}
                  </button>`;
                } else {
                  return `<button class="fav empty" data-slot="${slot}" title="Slot ${slot} is empty">
                    <span class="fav-num">${slot}</span>(empty)
                  </button>`;
                }
              }).join('')}
            </div>
          </div>
          ` : ''}

          ${this._config.show_source && sourceList.length ? `
          <div class="section">
            <div class="section-title">📡 Source</div>
            <select class="src" data-action="source">
              ${sourceList.map(s =>
                `<option value="${this._esc(s)}" ${s === src ? 'selected' : ''}>${this._esc(s)}</option>`
              ).join('')}
            </select>
          </div>
          ` : ''}
        </div>
      </ha-card>
    `;

    this._attachListeners();
  }

  _esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  _attachListeners() {
    const root = this.shadowRoot;
    const ent  = this._config.entity;
    const hass = this._hass;
    if (!hass) return;

    // Action buttons (prev, play, next, mute, heart)
    root.querySelectorAll('[data-action]').forEach(el => {
      el.addEventListener(el.type === 'range' ? 'change' : 'click', e => {
        const action = e.currentTarget.dataset.action;
        switch (action) {
          case 'prev':
            hass.callService('media_player', 'media_previous_track', { entity_id: ent });
            break;
          case 'next':
            hass.callService('media_player', 'media_next_track', { entity_id: ent });
            break;
          case 'play_pause':
            hass.callService('media_player', 'media_play_pause', { entity_id: ent });
            break;
          case 'mute':
            const muted = hass.states[ent].attributes.is_volume_muted;
            hass.callService('media_player', 'volume_mute',
              { entity_id: ent, is_volume_muted: !muted });
            break;
          case 'heart':
            // Use the new HA-side fav_save service — auto-picks the
            // next free slot and captures the currently playing URL.
            // Slot 0 = "auto-pick".
            hass.callService('lithe_audio', 'fav_save',
              { entity_id: ent, slot: 0 });
            break;
          case 'volume':
            const v = parseInt(e.currentTarget.value, 10);
            hass.callService('media_player', 'volume_set',
              { entity_id: ent, volume_level: v / 100 });
            break;
          case 'source':
            hass.callService('media_player', 'select_source',
              { entity_id: ent, source: e.currentTarget.value });
            break;
        }
      });
    });

    // Quick volume buttons
    root.querySelectorAll('button.qv').forEach(btn => {
      btn.addEventListener('click', () => {
        const v = parseInt(btn.dataset.vol, 10);
        hass.callService('media_player', 'volume_set',
          { entity_id: ent, volume_level: v / 100 });
      });
    });

    // Favourite buttons
    root.querySelectorAll('button.fav').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.classList.contains('empty')) {
          // Empty slot — save current playback to this slot instead
          const slot = parseInt(btn.dataset.slot, 10);
          hass.callService('lithe_audio', 'fav_save',
            { entity_id: ent, slot: slot });
          return;
        }
        const slot = parseInt(btn.dataset.slot, 10);
        // play_favourite tries HA-side local favs first, falls back to native
        hass.callService('lithe_audio', 'play_favourite',
          { entity_id: ent, slot: String(slot) });
      });
    });
  }
}

customElements.define('lithe-audio-card', LitheAudioCard);


// ── Visual Editor (GUI form for the card's config) ────────────────────
//
// HA shows this when the user clicks "Edit Card" or adds the card from
// the picker. It's a plain HTMLElement that emits 'config-changed' events
// when the user changes anything.

class LitheAudioCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass) return;

    // Build dropdown of all media_player entities
    const mediaPlayers = Object.keys(this._hass.states)
      .filter(eid => eid.startsWith('media_player.'))
      .sort();

    const c = this._config;
    this.innerHTML = `
      <div style="display:flex; flex-direction:column; gap:12px; padding:8px;">
        <ha-selector
          .hass=${'${this._hass}'}
          .label=${'"Speaker (media_player entity)"'}
          .value=${'"' + (c.entity || '') + '"'}
          .selector=${'${{entity: {domain: "media_player"}}}'}
        ></ha-selector>

        <div>
          <label style="display:block;font-size:12px;opacity:0.7;margin-bottom:4px;">
            Speaker (media_player entity)
          </label>
          <select id="entity" style="width:100%;padding:8px;">
            ${mediaPlayers.map(eid => {
              const friendly = this._hass.states[eid].attributes.friendly_name || eid;
              const sel = eid === c.entity ? 'selected' : '';
              return `<option value="${eid}" ${sel}>${friendly} (${eid})</option>`;
            }).join('')}
          </select>
        </div>

        <div>
          <label style="display:block;font-size:12px;opacity:0.7;margin-bottom:4px;">
            Card name (optional override)
          </label>
          <input id="name" type="text" value="${c.name || ''}"
                 placeholder="e.g. Kitchen Speaker"
                 style="width:100%;padding:8px;box-sizing:border-box;" />
        </div>

        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input id="show_artwork" type="checkbox"
                 ${c.show_artwork !== false ? 'checked' : ''} />
          <span>Show artwork thumbnail</span>
        </label>

        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input id="show_quick_volumes" type="checkbox"
                 ${c.show_quick_volumes !== false ? 'checked' : ''} />
          <span>Show quick volume buttons (0/20/40/60/80/100%)</span>
        </label>

        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input id="show_favourites" type="checkbox"
                 ${c.show_favourites !== false ? 'checked' : ''} />
          <span>Show 9 favourite slots</span>
        </label>

        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input id="show_source" type="checkbox"
                 ${c.show_source !== false ? 'checked' : ''} />
          <span>Show source switcher dropdown</span>
        </label>
      </div>
    `;

    // Wire all inputs to emit config-changed
    const fire = () => {
      const newConfig = {
        type: 'custom:lithe-audio-card',
        entity:             this.querySelector('#entity').value,
        name:               this.querySelector('#name').value || undefined,
        show_artwork:       this.querySelector('#show_artwork').checked,
        show_quick_volumes: this.querySelector('#show_quick_volumes').checked,
        show_favourites:    this.querySelector('#show_favourites').checked,
        show_source:        this.querySelector('#show_source').checked,
      };
      // Strip undefined fields for cleaner YAML
      Object.keys(newConfig).forEach(k =>
        newConfig[k] === undefined && delete newConfig[k]
      );
      const event = new CustomEvent('config-changed', {
        bubbles: true, composed: true,
        detail: { config: newConfig },
      });
      this.dispatchEvent(event);
    };

    this.querySelector('#entity').addEventListener('change', fire);
    this.querySelector('#name').addEventListener('input', fire);
    ['#show_artwork', '#show_quick_volumes',
     '#show_favourites', '#show_source'].forEach(sel => {
      this.querySelector(sel).addEventListener('change', fire);
    });
  }
}

customElements.define('lithe-audio-card-editor', LitheAudioCardEditor);


// ── Register with HA's card picker ────────────────────────────────────
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'lithe-audio-card',
  name: 'Lithe Audio',
  description: 'Branded media controller for Lithe Audio speakers',
  // preview: true makes HA render a thumbnail in the card picker
  preview: true,
  documentationURL: 'https://github.com/LitheAudio-Official/home-assistant-integration',
});

console.info(
  '%c LITHE-AUDIO-CARD %c v1.0.0 ',
  'color: white; background: #28a76d; font-weight: 700;',
  'color: #28a76d; background: white; font-weight: 700;'
);

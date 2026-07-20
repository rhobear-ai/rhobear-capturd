/* ═══════════════════════════════════════════════════════════════════
   RHOBEAR Captur'd — Premium Layer 1 Bridge
   Added 2026-07-20 · loaded LAST after all other scripts

   Maps redesign styling/attributes onto the real DOM elements.
   Pure additive layer — never replaces or rewrites existing JS.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Constellation bear SVG ── */
  var CONSTELLATION_BEAR =
    '<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<circle cx="50" cy="18" r="2.5" fill="#4a9eff"/>' +
    '<circle cx="32" cy="38" r="2" fill="#4a9eff"/>' +
    '<circle cx="68" cy="38" r="2" fill="#4a9eff"/>' +
    '<circle cx="30" cy="60" r="2" fill="#4a9eff"/>' +
    '<circle cx="70" cy="60" r="2" fill="#4a9eff"/>' +
    '<circle cx="50" cy="50" r="2" fill="#4a9eff"/>' +
    '<circle cx="50" cy="68" r="2" fill="#4a9eff"/>' +
    '<circle cx="38" cy="80" r="2" fill="#4a9eff"/>' +
    '<circle cx="62" cy="80" r="2" fill="#4a9eff"/>' +
    '<line x1="50" y1="18" x2="32" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.4"/>' +
    '<line x1="50" y1="18" x2="68" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.4"/>' +
    '<line x1="32" y1="38" x2="68" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="32" y1="38" x2="50" y2="50" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="68" y1="38" x2="50" y2="50" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="32" y1="38" x2="30" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="68" y1="38" x2="70" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="50" y1="50" x2="30" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.2"/>' +
    '<line x1="50" y1="50" x2="70" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.2"/>' +
    '<line x1="30" y1="60" x2="50" y2="68" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="70" y1="60" x2="50" y2="68" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="50" y1="68" x2="38" y2="80" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="50" y1="68" x2="62" y2="80" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '</svg>';

  var FILMING_BEAR =
    '<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">' +
    '<circle cx="50" cy="18" r="2.5" fill="#4a9eff"/>' +
    '<circle cx="32" cy="38" r="2" fill="#4a9eff"/>' +
    '<circle cx="68" cy="38" r="2" fill="#4a9eff"/>' +
    '<circle cx="30" cy="60" r="2" fill="#4a9eff"/>' +
    '<circle cx="70" cy="60" r="2" fill="#4a9eff"/>' +
    '<circle cx="50" cy="50" r="2" fill="#4a9eff"/>' +
    '<circle cx="50" cy="68" r="2" fill="#4a9eff"/>' +
    '<circle cx="38" cy="80" r="2" fill="#4a9eff"/>' +
    '<circle cx="62" cy="80" r="2" fill="#4a9eff"/>' +
    '<line x1="50" y1="18" x2="32" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.5"/>' +
    '<line x1="50" y1="18" x2="68" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.5"/>' +
    '<line x1="32" y1="38" x2="68" y2="38" stroke="#4a9eff" stroke-width="0.8" opacity="0.4"/>' +
    '<line x1="32" y1="38" x2="50" y2="50" stroke="#4a9eff" stroke-width="0.8" opacity="0.4"/>' +
    '<line x1="68" y1="38" x2="50" y2="50" stroke="#4a9eff" stroke-width="0.8" opacity="0.4"/>' +
    '<line x1="32" y1="38" x2="30" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.35"/>' +
    '<line x1="68" y1="38" x2="70" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.35"/>' +
    '<line x1="50" y1="50" x2="30" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="50" y1="50" x2="70" y2="60" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="30" y1="60" x2="50" y2="68" stroke="#4a9eff" stroke-width="0.8" opacity="0.35"/>' +
    '<line x1="70" y1="60" x2="50" y2="68" stroke="#4a9eff" stroke-width="0.8" opacity="0.35"/>' +
    '<line x1="50" y1="68" x2="38" y2="80" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '<line x1="50" y1="68" x2="62" y2="80" stroke="#4a9eff" stroke-width="0.8" opacity="0.3"/>' +
    '</svg>';

  /* ── DOM helpers ── */
  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return document.querySelectorAll(s); };

  /* ── 1. Inject constellation bear decoration ── */
  (function () {
    var wrap = $('.wrap');
    if (!wrap) return;
    var el = document.createElement('div');
    el.className = 'capturd-constellation';
    el.innerHTML = CONSTELLATION_BEAR;
    wrap.appendChild(el);
  })();

  /* ── 2. Inject film overlay content ── */
  (function () {
    var overlay = $('#capturdFilming');
    if (!overlay) return;
    // Set bear SVG
    var bearArea = overlay.querySelector('.capturd-filming__bear');
    if (bearArea) {
      bearArea.insertAdjacentHTML('beforeend', FILMING_BEAR);
    }
  })();

  /* ── 3. Wire filming overlay to existing poll() progress ── */
  (function () {
    var overlay = $('#capturdFilming');
    var statusEl = $('#capturdFilmingStatus');
    var fillEl = $('#capturdFilmingFill');
    var timeEl = $('#capturdFilmingTime');
    var doneEl = $('.capturd-filming__done');
    var stagesEl = overlay ? overlay.querySelector('.capturd-filming__stages') : null;
    var stopBtn = overlay ? overlay.querySelector('.capturd-filming__stop') : null;
    var prog = $('#prog');
    if (!overlay || !prog) return;

    var stageLabels = {
      queued: 'Scripting',
      running: 'Navigating',
      processing: 'Recording',
      rendering: 'Rendering'
    };
    var stageOrder = ['queued', 'running', 'processing', 'rendering', 'done'];

    function updateStage(status) {
      if (!stagesEl) return;
      var stageButtons = stagesEl.querySelectorAll('.capturd-stage');
      var currentIdx = stageOrder.indexOf(status);
      if (currentIdx < 0) currentIdx = 0;

      stageButtons.forEach(function (btn, i) {
        btn.classList.remove('capturd-stage--done', 'capturd-stage--active', 'capturd-stage--pending');
        if (i < Math.min(currentIdx, 4)) btn.classList.add('capturd-stage--done');
        else if (i === Math.min(currentIdx, 4) && i < 4) btn.classList.add('capturd-stage--active');
        else btn.classList.add('capturd-stage--pending');
      });
    }

    // Watch progress element for .on class changes
    var observer = new MutationObserver(function () {
      if (prog.classList.contains('on')) {
        // Filming started
        overlay.classList.add('on');
        if (statusEl) statusEl.textContent = 'Scripting…';
        if (fillEl) fillEl.style.width = '0%';
        if (doneEl) doneEl.classList.remove('on');
        if (stopBtn) stopBtn.textContent = 'Stop';
        updateStage('queued');
      } else {
        // Filming stopped/hidden
        overlay.classList.remove('on');
      }
    });
    observer.observe(prog, { attributes: true, attributeFilter: ['class'] });

    // Watch progText for status updates
    var progText = $('#progText');
    if (progText) {
      var textObserver = new MutationObserver(function () {
        if (!overlay.classList.contains('on')) return;
        var text = progText.textContent || '';
        var statusEl2 = statusEl;

        // Extract status from "Filming… (status)" or "The film crew is on set…"
        var match = text.match(/\(([^)]+)\)/);
        var statusText = match ? match[1] : null;

        if (statusText && statusEl2) {
          var label = stageLabels[statusText] || statusText;
          statusEl2.textContent = label + '…';
          updateStage(statusText);

          // Update progress fill (approximate from stage)
          var idx = stageOrder.indexOf(statusText);
          if (idx >= 0 && fillEl) {
            var pct = Math.min(100, (idx / 4) * 100);
            fillEl.style.width = pct + '%';
          }
        }

        // Update time estimate
        if (timeEl) {
          if (statusText === 'done') {
            timeEl.textContent = 'Complete';
          } else {
            timeEl.textContent = 'Filming…';
          }
        }

        // Done state
        if (statusText === 'done' && doneEl) {
          doneEl.classList.add('on');
          if (stopBtn) stopBtn.textContent = 'View';
        }
      });
      textObserver.observe(progText, { childList: true, characterData: true, subtree: true });
    }

    // Stop button handler
    if (stopBtn) {
      stopBtn.addEventListener('click', function () {
        if (stopBtn.textContent === 'View') {
          overlay.classList.remove('on');
          return;
        }
        overlay.classList.remove('on');
        // The existing endRun() handles the real stop logic
      });
    }

    // Watch genState for "Done" to show completion in overlay
    var genState = $('#genState');
    if (genState) {
      var stateObserver = new MutationObserver(function () {
        if (!overlay.classList.contains('on')) return;
        var text = genState.textContent || genState.innerHTML || '';
        if (text.indexOf('Done') >= 0 || text.indexOf('done') >= 0) {
          if (doneEl) {
            doneEl.classList.add('on');
          }
          if (statusEl) statusEl.textContent = 'Demo ready!';
          if (fillEl) fillEl.style.width = '100%';
          if (timeEl) timeEl.textContent = 'Complete';
          if (stopBtn) stopBtn.textContent = 'View';
          updateStage('done');
        }
      });
      stateObserver.observe(genState, { childList: true, characterData: true, subtree: true });
    }
  })();

  /* ── 4. Gallery enhancements — add play button + action buttons ── */
  (function () {
    function enhanceGallery() {
      $$('.galitem').forEach(function (item) {
        // Skip if already enhanced
        if (item.querySelector('.capturd-play-btn')) return;

        // Add play button overlay
        var playBtn = document.createElement('div');
        playBtn.className = 'capturd-play-btn';
        playBtn.innerHTML =
          '<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6,3 20,12 6,21"/></svg>';
        item.appendChild(playBtn);

        // Add action buttons
        var meta = item.querySelector('.meta');
        if (meta) {
          var actions = document.createElement('div');
          actions.className = 'capturd-actions on';
          actions.innerHTML =
            '<button class="capturd-action capturd-action--watch" data-action="watch">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<polygon points="5,3 19,12 5,21"/></svg>Watch</button>' +
            '<button class="capturd-action capturd-action--ghost" data-action="download">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/>' +
            '<line x1="12" y1="15" x2="12" y2="3"/></svg>Download</button>';
          meta.parentNode.insertBefore(actions, meta.nextSibling);
        }

        // Wire watch button to existing video player
        item.addEventListener('click', function (e) {
          var target = e.target.closest('button');
          if (target && target.dataset.action === 'watch') {
            var video = item.querySelector('video');
            if (video) {
              // Scroll result into view and play
              var result = $('#result');
              if (result) {
                result.classList.add('on');
                var mainVideo = $('#video');
                if (mainVideo) mainVideo.src = video.src;
                result.scrollIntoView({ behavior: 'smooth' });
              }
            }
          }
        });
      });
    }

    // Run on load
    enhanceGallery();

    // Watch for gallery refreshes (loadGallery() replaces innerHTML)
    var galgrid = $('#galgrid');
    if (galgrid) {
      var galObserver = new MutationObserver(function () {
        enhanceGallery();
      });
      galObserver.observe(galgrid, { childList: true, subtree: false });
    }
  })();

  /* ── 5. Ask Rho FAB handler ── */
  (function () {
    var fab = $('#capturdFab');
    if (!fab) return;

    fab.addEventListener('click', function () {
      // Try triggering the Rho companion if available
      if (window.RHOBEAR_COMPANION && window.RHOBEAR_COMPANION.ready) {
        // Dispatch a custom event that companion-embed.js might listen for
        document.dispatchEvent(new CustomEvent('rho:ask', { detail: { source: 'fab' } }));
      }
      // Fallback: show a brief toast
      if (typeof toast === 'function') {
        toast('Ask Rho — the AI director is listening.');
      }
    });
  })();

  /* ── 6. Keyboard: Escape to close filming overlay ── */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var overlay = $('#capturdFilming');
      if (overlay && overlay.classList.contains('on')) {
        overlay.classList.remove('on');
      }
    }
  });

})();

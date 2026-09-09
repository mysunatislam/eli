(() => {
  const $ = (s) => document.querySelector(s);
  let provider = null;

  const links = { gemini: 'https://aistudio.google.com/apikey', anthropic: 'https://console.anthropic.com/settings/keys' };
  const labels = { gemini: 'Gemini API key', anthropic: 'Anthropic API key' };

  document.querySelectorAll('.opt[data-provider]').forEach((el) => {
    el.addEventListener('click', () => {
      document.querySelectorAll('.opt').forEach((o) => o.classList.remove('selected'));
      el.classList.add('selected');
      provider = el.dataset.provider;
      const keyRow = $('#keyRow');
      if (provider) {
        keyRow.hidden = false;
        $('#keyLabel').textContent = labels[provider];
        $('#keyLink').textContent = new URL(links[provider]).hostname;
        $('#keyLink').href = links[provider];
        $('#key').focus();
      } else {
        keyRow.hidden = true;
      }
      $('#err1').textContent = '';
    });
  });
  $('#keyLink').addEventListener('click', (e) => { e.preventDefault(); window.setup.openExternal(links[provider]); });

  $('#continueBtn').addEventListener('click', () => {
    if (provider && !$('#key').value.trim()) {
      $('#err1').textContent = 'Paste your key, or choose “Skip for now”.';
      return;
    }
    $('#step1').classList.remove('active');
    $('#step2').classList.add('active');
  });
  $('#backBtn').addEventListener('click', () => {
    $('#step2').classList.remove('active');
    $('#step1').classList.add('active');
  });

  $('#finishBtn').addEventListener('click', async () => {
    $('#finishBtn').disabled = true;
    $('#backBtn').disabled = true;
    $('#progress').classList.add('active');
    $('#err2').textContent = '';
    try {
      const result = await window.setup.finish({
        provider, key: provider ? $('#key').value.trim() : '',
        autostart: $('#cbAutostart').checked, voice: $('#cbVoice').checked,
      });
      if (!result.ok) {
        $('#progress').classList.remove('active');
        $('#err2').textContent = result.error || 'Something went wrong starting Eli.';
        $('#finishBtn').disabled = false;
        $('#backBtn').disabled = false;
      }
      // on success the main process closes this window once the overlay is up
    } catch (e) {
      $('#progress').classList.remove('active');
      $('#err2').textContent = String(e);
      $('#finishBtn').disabled = false;
      $('#backBtn').disabled = false;
    }
  });

  window.setup.onProgress((text) => { $('#progressText').textContent = text; });
  $('#opt-skip').click();
})();

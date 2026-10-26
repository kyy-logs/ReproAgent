const copyButtons = document.querySelectorAll('[data-copy]');

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

copyButtons.forEach((button) => {
  button.addEventListener('click', async () => {
    const container = button.closest('.code-block, .inline-copy');
    const source = container?.querySelector('pre code, code');
    const text = source?.textContent?.trim();
    if (!text) return;

    const original = button.textContent;
    try {
      await copyText(text);
      button.textContent = '已复制';
    } catch {
      button.textContent = '复制失败';
    }
    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
});

const installPlatformTabs = Array.from(document.querySelectorAll('[data-install-platform]'));
const installPlatformPanels = Array.from(document.querySelectorAll('[data-install-panel]'));
installPlatformTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const platform = tab.dataset.installPlatform;
    installPlatformTabs.forEach((button) => {
      const active = button === tab;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', String(active));
    });
    installPlatformPanels.forEach((panel) => {
      panel.hidden = panel.dataset.installPanel !== platform;
    });
  });
});

const pipeline = document.querySelector('.pipeline');
if (pipeline && 'IntersectionObserver' in window) {
  document.documentElement.classList.add('js');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.2 });
  observer.observe(pipeline);
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const macModes = {
  'claude-code': {
    shell: 'claude',
    skill: '/reproagent 帮我复现当前项目里的这个 Bug',
  },
  codex: {
    shell: 'codex',
    skill: '$reproagent 帮我复现当前项目里的这个 Bug',
  },
  cli: {
    shell: 'reproagent run --config task.json',
    skill: '',
  },
};

let pipParallaxPaused = false;

const mac = document.querySelector('[data-mac-demo]');
if (mac) {
  const macCommand = mac.querySelector('[data-mac-command]');
  const macSkill = mac.querySelector('[data-mac-skill]');
  const macClaude = mac.querySelector('[data-mac-claude]');
  const macLog = mac.querySelector('[data-mac-log]');
  const macLogStatic = macLog.innerHTML;
  const macVerdict = mac.querySelector('[data-mac-verdict]');
  const macFiles = mac.querySelector('[data-mac-files]');
  const macReplay = mac.querySelector('[data-mac-replay]');
  const macFrame = mac.closest('[data-mac-frame]');
  const macCursor = macFrame?.querySelector('[data-mac-cursor]');
  const macPipFloat = macFrame?.querySelector('[data-mac-pip-float]');
  const macModeButtons = Array.from(document.querySelectorAll('[data-mac-mode]'));
  const waitMac = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  function setMacCursor(x, y, duration = 700) {
    if (!macCursor) return;
    macCursor.style.setProperty('--cursor-duration', `${duration}ms`);
    macCursor.style.setProperty('--cursor-x', `${x}%`);
    macCursor.style.setProperty('--cursor-y', `${y}%`);
  }

  function showMacCursor() { macCursor?.classList.add('is-visible'); }
  function hideMacCursor() { macCursor?.classList.remove('is-visible', 'is-grabbing'); }

  function resetMacCursor() {
    hideMacCursor();
    macPipFloat?.classList.remove('is-scripted-drag');
    macPipFloat?.style.setProperty('--pip-drag-x', '0px');
    macPipFloat?.style.setProperty('--pip-drag-y', '0px');
    macFrame?.style.setProperty('--pip-x', '0px');
    macFrame?.style.setProperty('--pip-y', '0px');
    macFrame?.style.setProperty('--pip-tilt-x', '0deg');
    macFrame?.style.setProperty('--pip-tilt-y', '0deg');
    macCursor?.style.setProperty('--cursor-duration', '0ms');
    macCursor?.style.setProperty('--cursor-x', '64%');
    macCursor?.style.setProperty('--cursor-y', '68%');
    pipParallaxPaused = false;
  }
  let macToken = 0;
  let macInView = false;
  let restartMac = () => {};
  macLog.scrollTop = macLog.scrollHeight;

  function appendMacLog(kind, text) {
    const line = document.createElement('div');
    line.className = 'log-line' + (kind ? ' ' + kind : '');
    const marker = document.createElement('span');
    marker.className = 'log-marker';
    const body = document.createElement('span');
    body.textContent = text;
    line.append(marker, body);
    macLog.append(line);
    macLog.scrollTop = macLog.scrollHeight;
    return line;
  }

  const macSteps = [
    ['step', 'reading issue and repository facts', 520],
    ['step', 'exploring code and building the contract', 620],
    ['step', 'writing the candidate pytest', 420],
    ['log-diff', '+ testing/test_repro_default_log_format.py', 180],
    ['log-diff', '+ assert "%(name)s" in DEFAULT_LOG_FORMAT', 220],
    ['step', 'running on the buggy version', 420],
    ['log-fail', 'FAILED testing/test_repro_default_log_format.py::test_format_has_name_field', 180],
    ['log-fail', '3 failed, 1 passed in 0.41s', 220],
    ['step', 'rerunning in a fresh copy', 420],
    ['log-fail', '3 failed, 1 passed in 0.47s', 220],
    ['step', 'validating the fixed version', 420],
    ['log-pass', '4 passed in 0.12s', 220],
    ['step', 'sealing the evidence package', 420],
  ];

  function resetMacStatic() {
    mac.classList.remove('running');
    macFrame?.classList.remove('running', 'show-report');
    macCommand.textContent = macCommand.dataset.macCommand || '';
    macSkill.textContent = macSkill.dataset.macSkill || '';
    macClaude.classList.remove('is-visible');
    macLog.innerHTML = macLogStatic;
    macLog.scrollTop = macLog.scrollHeight;
    macVerdict.classList.remove('is-visible');
    macFiles.classList.remove('is-visible');
    resetMacCursor();
  }

  async function playMac(token) {
    if (!macInView) return;
    const shellText = macCommand.dataset.macCommand || '';
    const skillText = macSkill.dataset.macSkill || '';
    mac.classList.add('running');
    macFrame?.classList.add('running');
    macFrame?.classList.remove('show-report');
    mac.classList.toggle('is-cli', !skillText);
    macCommand.textContent = '';
    macSkill.textContent = '';
    macClaude.classList.remove('is-visible');
    macLog.innerHTML = '';
    macLog.scrollTop = 0;
    macVerdict.classList.remove('is-visible');
    macFiles.classList.remove('is-visible');
    resetMacCursor();
    showMacCursor();
    setMacCursor(64, 72, 700);

    await waitMac(280);
    for (const character of shellText) {
      if (token !== macToken) return;
      macCommand.textContent += character;
      await waitMac(30);
    }

    if (skillText) {
      await waitMac(280);
      macClaude.classList.add('is-visible');
      await waitMac(220);
      for (const character of skillText) {
        if (token !== macToken) return;
        macSkill.textContent += character;
        await waitMac(22);
      }
    }

    await waitMac(220);
    let stepIndex = 0;
    for (const [kind, text, delay] of macSteps) {
      if (token !== macToken) return;
      if (kind === 'step') {
        const line = appendMacLog('active', text);
        if (stepIndex === 5) setMacCursor(57, 53, 900);
        await waitMac(delay);
        if (token !== macToken) return;
        line.classList.remove('active');
        line.classList.add('done');
      } else {
        appendMacLog(kind, text);
        await waitMac(delay);
      }
      stepIndex += 1;
    }

    macVerdict.classList.add('is-visible');
    await waitMac(360);
    macFiles.classList.add('is-visible');
    await waitMac(260);
    macFrame?.classList.add('show-report');
    if (macCursor && macPipFloat) {
      await waitMac(300);
      if (token !== macToken) return;
      setMacCursor(62, 63, 600);
      await waitMac(700);
      if (token !== macToken) return;
      macCursor.classList.add('is-grabbing');
      pipParallaxPaused = true;
      macFrame?.style.setProperty('--pip-x', '0px');
      macFrame?.style.setProperty('--pip-y', '0px');
      macFrame?.style.setProperty('--pip-tilt-x', '0deg');
      macFrame?.style.setProperty('--pip-tilt-y', '0deg');
      macPipFloat.classList.add('is-scripted-drag');
      const dragX = -(macFrame?.clientWidth || 600) * 0.06;
      const dragY = -(macFrame?.clientHeight || 450) * 0.035;
      macPipFloat.style.setProperty('--pip-drag-x', `${dragX.toFixed(1)}px`);
      macPipFloat.style.setProperty('--pip-drag-y', `${dragY.toFixed(1)}px`);
      setMacCursor(56, 59.5, 900);
      await waitMac(950);
      if (token !== macToken) return;
      macCursor.classList.remove('is-grabbing');
      macPipFloat.classList.remove('is-scripted-drag');
      await waitMac(350);
      setMacCursor(70, 76, 600);
      await waitMac(550);
      hideMacCursor();
      await waitMac(550);
    } else {
      await waitMac(3400);
    }
    macFrame?.classList.remove('show-report');
    await waitMac(300);
    if (token !== macToken || !macInView) return;
    playMac(token);
  }

  restartMac = () => {
    macToken += 1;
    playMac(macToken);
  };

  function setMacMode(mode) {
    const config = macModes[mode];
    if (!config) return;
    macCommand.dataset.macCommand = config.shell;
    macSkill.dataset.macSkill = config.skill;
    macCommand.textContent = config.shell;
    macSkill.textContent = config.skill;
    mac.classList.toggle('is-cli', !config.skill);
    macModeButtons.forEach((button) => {
      const active = button.dataset.macMode === mode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    restartMac();
  }

  macModeButtons.forEach((button) => {
    button.addEventListener('click', () => setMacMode(button.dataset.macMode));
  });

  if (!reducedMotion.matches) {
    macReplay?.addEventListener('click', restartMac);
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            macInView = true;
            restartMac();
          } else {
            macInView = false;
            macToken += 1;
            resetMacStatic();
          }
        });
      }, { threshold: 0.25 });
      observer.observe(mac);
    } else {
      macInView = true;
      restartMac();
    }
  }
}

const tiltArea = document.querySelector('.hero-terminal');
const tiltFrame = tiltArea?.querySelector('[data-mac-frame]');
const finePointer = window.matchMedia('(pointer: fine)');

if (tiltArea && tiltFrame && finePointer.matches) {
  let targetX = 0;
  let targetY = 0;
  let currentX = 0;
  let currentY = 0;
  let tiltFrameId = 0;

  const tickTilt = () => {
    currentX += (targetX - currentX) * 0.14;
    currentY += (targetY - currentY) * 0.14;
    tiltFrame.style.setProperty('--tilt-y', `${(currentX * 9).toFixed(2)}deg`);
    tiltFrame.style.setProperty('--tilt-x', `${(-currentY * 9).toFixed(2)}deg`);
    if (!pipParallaxPaused) {
      tiltFrame.style.setProperty('--pip-tilt-y', `${(currentX * 6).toFixed(2)}deg`);
      tiltFrame.style.setProperty('--pip-tilt-x', `${(-currentY * 6).toFixed(2)}deg`);
      tiltFrame.style.setProperty('--pip-x', `${(currentX * 40).toFixed(2)}px`);
      tiltFrame.style.setProperty('--pip-y', `${(currentY * 40).toFixed(2)}px`);
    }

    if (Math.abs(targetX - currentX) < 0.0005 && Math.abs(targetY - currentY) < 0.0005) {
      currentX = targetX;
      currentY = targetY;
      tiltFrameId = 0;
      return;
    }
    tiltFrameId = window.requestAnimationFrame(tickTilt);
  };

  const startTilt = () => {
    if (!tiltFrameId) tiltFrameId = window.requestAnimationFrame(tickTilt);
  };

  const resetTilt = () => {
    targetX = 0;
    targetY = 0;
    tiltFrame.classList.remove('is-pointer');
    startTilt();
  };

  tiltArea.addEventListener('pointermove', (event) => {
    if (reducedMotion.matches) return;
    const rect = tiltFrame.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const nx = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const ny = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    targetX = nx - 0.5;
    targetY = ny - 0.5;
    tiltFrame.style.setProperty('--glow-x', `${(nx * 100).toFixed(1)}%`);
    tiltFrame.style.setProperty('--glow-y', `${(ny * 100).toFixed(1)}%`);
    tiltFrame.classList.add('is-pointer');
    startTilt();
  }, { passive: true });

  tiltArea.addEventListener('pointerleave', resetTilt);
  window.addEventListener('blur', resetTilt);
}

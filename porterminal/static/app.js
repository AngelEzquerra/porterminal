/**
 * Porterminal - Web-based terminal client with multi-tab support
 */

// Tab state
const tabs = [];
let activeTabId = null;
let tabCounter = 0;

// LocalStorage key for persisting tabs
const STORAGE_KEY = 'porterminal-tabs';

/**
 * Save tab state to localStorage for session persistence across refreshes
 */
function saveTabsToStorage() {
    const tabData = tabs.map(tab => ({
        id: tab.id,
        shellId: tab.shellId,
        sessionId: tab.sessionId,
    }));
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            tabs: tabData,
            activeTabId: activeTabId,
            tabCounter: tabCounter,
        }));
    } catch (e) {
        console.warn('Failed to save tabs to localStorage:', e);
    }
}

/**
 * Load tab state from localStorage
 */
function loadTabsFromStorage() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            return JSON.parse(stored);
        }
    } catch (e) {
        console.warn('Failed to load tabs from localStorage:', e);
    }
    return null;
}

// Current active tab references (shortcuts)
let term = null;
let ws = null;
let sessionId = null;
let fitAddonRef = null;

// Connection settings
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 1000;


// Modifier key state: 'off' | 'sticky' | 'locked'
const modifiers = {
    ctrl: 'off',
    alt: 'off',
    shift: 'off',
};

// Last tap time for double-tap detection
const lastTapTime = {
    ctrl: 0,
    alt: 0,
    shift: 0,
    escape: 0,
};
const DOUBLE_TAP_MS = 300;

// Text encoder for WebSocket binary messages
const textEncoder = new TextEncoder();

// Client-side heartbeat
const HEARTBEAT_MS = 25000;

/**
 * Configure terminal textarea for mobile devices
 *
 * Problem: iOS Safari shows password/card autofill prompts on the xterm.js
 * hidden textarea, preventing normal keyboard input.
 *
 * Solution approach (based on Safari behavior research):
 * - Safari ignores autocomplete="off" by design (Apple security policy)
 * - Safari scans name, placeholder, label attributes for trigger keywords
 * - Safari treats UNRECOGNIZED autocomplete values as neutral (no autofill)
 * - iOS predictive text requires autocorrect="on" (Safari-specific attribute)
 *
 * References:
 * - Apple Safari HTML Reference: autocorrect, autocapitalize attributes
 * - xterm.js issue #2403: Mobile predictive keyboard accommodation
 * - WebKit behavior: unrecognized autocomplete values bypass autofill
 */
function configureTerminalTextarea(textarea) {
    // === Prevent password/autofill detection ===
    // Use unrecognized value - Safari treats unknown values as "do nothing"
    // This is more reliable than "off" which Safari deliberately ignores
    textarea.setAttribute('autocomplete', 'terminal');
    textarea.setAttribute('type', 'text');
    // Neutral name that won't trigger keyword-based autofill detection
    textarea.setAttribute('name', 'xterm');

    // === iOS keyboard behavior ===
    // autocorrect: Safari-specific, enables word suggestions when "on"
    textarea.setAttribute('autocorrect', 'on');
    // autocapitalize: use "none" (not "off" which is deprecated since iOS 5)
    textarea.setAttribute('autocapitalize', 'none');
    // spellcheck: disable red underlines (distracting in terminal)
    textarea.setAttribute('spellcheck', 'false');
    // inputmode: standard text keyboard layout
    textarea.setAttribute('inputmode', 'text');
    // enterkeyhint: iOS keyboard shows "Send" on return key
    textarea.setAttribute('enterkeyhint', 'send');

    // === Accessibility (avoid triggering secure input detection) ===
    textarea.setAttribute('role', 'textbox');
    textarea.setAttribute('aria-label', 'Terminal input');
    textarea.setAttribute('aria-multiline', 'false');
    textarea.removeAttribute('aria-hidden');

    // === Password manager browser extensions ===
    textarea.setAttribute('data-form-type', 'other');
    textarea.setAttribute('data-lpignore', 'true');         // LastPass
    textarea.setAttribute('data-1p-ignore', 'true');        // 1Password
    textarea.setAttribute('data-bwignore', 'true');         // Bitwarden
    textarea.setAttribute('data-protonpass-ignore', 'true'); // ProtonPass
    textarea.setAttribute('data-dashlane-ignore', 'true');  // Dashlane

    // === CSS override for iOS secure text styling ===
    textarea.style.setProperty('-webkit-text-security', 'none', 'important');
}

/**
 * Send input to active tab
 */
function sendInput(data) {
    const tab = tabs.find(t => t.id === activeTabId);
    if (tab) {
        sendInputToTab(tab, data);
    }
}

// Centralized resize debouncer - prevents double-firing and coalesces rapid resizes
const resizeState = {
    pending: new Map(),  // tabId -> timeout
    lastSent: new Map(), // tabId -> {cols, rows, time}
};

/**
 * Debounced resize for a tab - coalesces rapid resize events
 */
function scheduleResize(tab, delay = 50) {
    if (!tab) return;

    // Clear any pending resize for this tab
    if (resizeState.pending.has(tab.id)) {
        clearTimeout(resizeState.pending.get(tab.id));
    }

    // Schedule new resize
    const timeout = setTimeout(() => {
        resizeState.pending.delete(tab.id);

        // Get current dimensions
        const cols = tab.term.cols;
        const rows = tab.term.rows;

        // Check if dimensions actually changed since last send
        const last = resizeState.lastSent.get(tab.id);
        if (last && last.cols === cols && last.rows === rows) {
            return; // No change, skip
        }

        // Send resize
        if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
            tab.ws.send(JSON.stringify({
                type: 'resize',
                cols: cols,
                rows: rows,
            }));
            resizeState.lastSent.set(tab.id, { cols, rows, time: Date.now() });
        }
    }, delay);

    resizeState.pending.set(tab.id, timeout);
}

/**
 * Update connection status display
 */
function setConnectionStatus(status) {
    const dot = document.getElementById('connection-dot');
    if (dot) {
        dot.className = status === 'connected' ? 'connected' : '';
    }
}

/**
 * Key mapping
 */
const KEY_MAP = {
    'Tab': '\t',
    'Enter': '\r',
    'Backspace': '\x7f',
    'Escape': '\x1b',
    'Space': ' ',
    'ArrowUp': '\x1b[A',
    'ArrowDown': '\x1b[B',
    'ArrowRight': '\x1b[C',
    'ArrowLeft': '\x1b[D',
    'Ctrl+C': '\x03',
    'Ctrl+D': '\x04',
    'Ctrl+Z': '\x1a',
    'Ctrl+L': '\x0c',
    'Ctrl+R': '\x12',
    'Ctrl+A': '\x01',
    'Ctrl+E': '\x05',
    'Ctrl+W': '\x17',
    'Ctrl+U': '\x15',
};

/**
 * Get key sequence with modifiers
 */
function getKeySequence(key) {
    // Check if it's a direct send (like | or `)
    const btn = document.querySelector(`[data-key="${key}"]`) ||
                document.querySelector(`[data-send="${key}"]`);
    if (btn && btn.dataset.send) {
        return btn.dataset.send;
    }

    // Check key map
    if (KEY_MAP[key]) {
        return KEY_MAP[key];
    }

    // Apply modifiers for single characters
    if (key.length === 1) {
        let char = key;

        if (modifiers.shift === 'sticky' || modifiers.shift === 'locked') {
            char = char.toUpperCase();
        }

        if (modifiers.ctrl === 'sticky' || modifiers.ctrl === 'locked') {
            // Ctrl+letter = letter code - 64 (for uppercase)
            const code = char.toUpperCase().charCodeAt(0);
            if (code >= 65 && code <= 90) {
                char = String.fromCharCode(code - 64);
            }
        }

        if (modifiers.alt === 'sticky' || modifiers.alt === 'locked') {
            char = '\x1b' + char;
        }

        return char;
    }

    return null;
}

/**
 * Handle key button press
 */
function handleKeyButton(key) {
    const sequence = getKeySequence(key);
    if (sequence) {
        sendInput(sequence);
        term.focus();
    }

    // Reset sticky modifiers
    for (const mod of ['ctrl', 'alt', 'shift']) {
        if (modifiers[mod] === 'sticky') {
            modifiers[mod] = 'off';
            updateModifierButton(mod);
        }
    }
}

/**
 * Handle modifier button tap
 */
function handleModifierTap(modifier) {
    const now = Date.now();
    const lastTap = lastTapTime[modifier];
    lastTapTime[modifier] = now;

    if (now - lastTap < DOUBLE_TAP_MS) {
        // Double tap - toggle lock
        modifiers[modifier] = modifiers[modifier] === 'locked' ? 'off' : 'locked';
    } else {
        // Single tap - cycle: off -> sticky -> off (or locked -> off)
        if (modifiers[modifier] === 'off') {
            modifiers[modifier] = 'sticky';
        } else {
            modifiers[modifier] = 'off';
        }
    }

    updateModifierButton(modifier);

    // Refocus terminal so keyboard input works
    if (term) term.focus();
}

/**
 * Update modifier button appearance
 */
function updateModifierButton(modifier) {
    const btn = document.getElementById(`btn-${modifier}`);
    if (!btn) return;

    btn.classList.remove('sticky', 'locked');

    switch (modifiers[modifier]) {
        case 'sticky':
            btn.classList.add('sticky');
            break;
        case 'locked':
            btn.classList.add('locked');
            break;
    }
}

/**
 * Load configuration and populate UI
 */
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();

        // Populate shell selector
        const shellSelect = document.getElementById('shell-select');
        shellSelect.innerHTML = '';
        for (const shell of config.shells) {
            const option = document.createElement('option');
            option.value = shell.id;
            option.textContent = shell.name;
            if (shell.id === config.default_shell) {
                option.selected = true;
            }
            shellSelect.appendChild(option);
        }

        return config;
    } catch (e) {
        console.error('Failed to load config:', e);
        return null;
    }
}

/**
 * Setup modifier button event listeners (Ctrl, Alt)
 */
function setupModifierButtons() {
    ['ctrl', 'alt'].forEach(mod => {
        const btn = document.getElementById(`btn-${mod}`);
        if (!btn) return;

        let touchUsed = false;

        btn.addEventListener('touchstart', (e) => {
            touchUsed = true;
            e.preventDefault();
        }, { passive: false });

        btn.addEventListener('touchend', (e) => {
            e.preventDefault();
            handleModifierTap(mod);
        }, { passive: false });

        btn.addEventListener('click', () => {
            if (!touchUsed) {
                handleModifierTap(mod);
            }
            touchUsed = false;
        });
    });
}

/**
 * Setup escape button with double-tap for double-escape
 */
function setupEscapeButton() {
    const btn = document.getElementById('btn-escape');
    if (!btn) return;

    let touchUsed = false;

    const handleEscapeTap = () => {
        const now = Date.now();
        const lastTap = lastTapTime.escape;
        lastTapTime.escape = now;

        if (now - lastTap < DOUBLE_TAP_MS) {
            // Double tap - send double escape
            sendInput('\x1b\x1b');
        } else {
            // Single tap - send single escape
            sendInput('\x1b');
        }

        if (term) term.focus();
    };

    btn.addEventListener('touchstart', (e) => {
        touchUsed = true;
        e.preventDefault();
    }, { passive: false });

    btn.addEventListener('touchend', (e) => {
        e.preventDefault();
        handleEscapeTap();
    }, { passive: false });

    btn.addEventListener('click', () => {
        if (!touchUsed) {
            handleEscapeTap();
        }
        touchUsed = false;
    });
}

/**
 * Setup button event listeners
 */
function setupButtons() {
    // Track if touch was used to prevent double-firing
    let touchUsed = false;

    // Paste button - with touch support
    const pasteBtn = document.getElementById('btn-paste');
    if (pasteBtn) {
        let pasteTouchUsed = false;

        const doPaste = async () => {
            try {
                if (!navigator.clipboard || !navigator.clipboard.readText) {
                    console.warn('Clipboard API not available');
                    if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
                    return;
                }
                const text = await navigator.clipboard.readText();
                if (text) {
                    sendInput(text);
                    if (navigator.vibrate) navigator.vibrate(30);
                } else {
                    if (navigator.vibrate) navigator.vibrate([30, 30]);
                }
            } catch (e) {
                console.error('Paste failed:', e);
                if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
            }
            if (term) term.focus();
        };

        pasteBtn.addEventListener('touchstart', (e) => {
            pasteTouchUsed = true;
            e.preventDefault();
        }, { passive: false });

        pasteBtn.addEventListener('touchend', (e) => {
            e.preventDefault();
            doPaste();
        }, { passive: false });

        pasteBtn.addEventListener('click', () => {
            if (!pasteTouchUsed) {
                doPaste();
            }
            pasteTouchUsed = false;
        });
    }

    // Tool buttons
    document.querySelectorAll('.tool-btn').forEach(btn => {
        if (btn.dataset.bound) return;
        btn.dataset.bound = 'true';

        const action = () => {
            if (btn.dataset.key) {
                handleKeyButton(btn.dataset.key);
            } else if (btn.dataset.send) {
                sendInput(btn.dataset.send);
                if (term) term.focus();
            }
        };

        btn.addEventListener('touchstart', (e) => {
            touchUsed = true;
            e.preventDefault();
        }, { passive: false });

        btn.addEventListener('touchend', (e) => {
            e.preventDefault();
            action();
        }, { passive: false });

        btn.addEventListener('click', (e) => {
            if (!touchUsed) {
                action();
            }
            touchUsed = false;
        });
    });

}

/**
 * Setup shell selector
 */
function setupShellSelector() {
    const shellSelect = document.getElementById('shell-select');
    shellSelect.addEventListener('change', () => {
        const shellId = shellSelect.value;
        if (shellId && activeTabId) {
            const tab = tabs.find(t => t.id === activeTabId);
            if (tab) {
                // Close current session and start new one with new shell
                tab.sessionId = null;
                tab.shellId = shellId;
                tab.inputBuffer = '';
                if (tab.inputTimeout) {
                    clearTimeout(tab.inputTimeout);
                    tab.inputTimeout = null;
                }
                // Use reset() instead of clear() to fully reset terminal state
                // This clears internal buffers and prevents escape sequence artifacts
                tab.term.reset();
                // Clear heartbeat interval from old connection
                if (tab.heartbeatInterval) {
                    clearInterval(tab.heartbeatInterval);
                    tab.heartbeatInterval = null;
                }
                if (tab.ws) {
                    // Prevent reconnect attempts from the old connection
                    const oldWs = tab.ws;
                    tab.ws = null;
                    oldWs.onclose = null;
                    oldWs.onerror = null;
                    oldWs.close();
                }
                // Small delay to ensure clean state before new connection
                setTimeout(() => connectTab(tab, shellId), 50);
            }
        }
    });
}

/**
 * Create a new tab
 * @param {string|null} shellId - Shell ID to use
 * @param {object|null} savedTab - Saved tab data for restoration (id, shellId, sessionId)
 */
function createTab(shellId = null, savedTab = null) {
    const id = savedTab?.id || ++tabCounter;
    if (savedTab?.id && savedTab.id > tabCounter) {
        tabCounter = savedTab.id;
    }
    const shell = savedTab?.shellId || shellId || document.getElementById('shell-select')?.value || 'powershell';

    // Create terminal container
    const container = document.createElement('div');
    container.id = `terminal-${id}`;
    container.className = 'terminal-instance';
    container.style.display = 'none';
    document.getElementById('terminal').appendChild(container);

    // Detect mobile
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

    // Create terminal instance with VSCode theme
    const terminal = new Terminal({
        cursorBlink: true,
        fontSize: isMobile ? 14 : 13,
        fontFamily: 'Menlo, Monaco, Consolas, monospace',
        theme: {
            background: '#1e1e1e',
            foreground: '#cccccc',
            cursor: '#aeafad',
            cursorAccent: '#1e1e1e',
            selection: 'rgba(38, 79, 120, 0.5)',
            black: '#000000',
            red: '#cd3131',
            green: '#0dbc79',
            yellow: '#e5e510',
            blue: '#2472c8',
            magenta: '#bc3fbc',
            cyan: '#11a8cd',
            white: '#e5e5e5',
            brightBlack: '#666666',
            brightRed: '#f14c4c',
            brightGreen: '#23d18b',
            brightYellow: '#f5f543',
            brightBlue: '#3b8eea',
            brightMagenta: '#d670d6',
            brightCyan: '#29b8db',
            brightWhite: '#e5e5e5',
        },
        scrollback: 5000,
        convertEol: true,
        allowProposedApi: true,
        // iOS touch selection
        rightClickSelectsWord: true,
        altClickMovesCursor: false,
        // Disable smooth scrolling for instant response
        smoothScrollDuration: 0,
        scrollSensitivity: 1,
        fastScrollSensitivity: 5,
    });

    // Load addons
    const fitAddon = new FitAddon.FitAddon();
    terminal.loadAddon(fitAddon);

    const webLinksAddon = new WebLinksAddon.WebLinksAddon();
    terminal.loadAddon(webLinksAddon);

    // Open terminal
    terminal.open(container);

    // Configure textarea for iOS/mobile - prevent password autofill, enable word suggestions
    const textarea = container.querySelector('.xterm-helper-textarea');
    if (textarea) {
        configureTerminalTextarea(textarea);
    }

    // Try WebGL on desktop
    if (!isMobile) {
        try {
            const webglAddon = new WebglAddon.WebglAddon();
            webglAddon.onContextLoss(() => webglAddon.dispose());
            terminal.loadAddon(webglAddon);
        } catch (e) {
            console.warn('WebGL not available');
        }
    }

    // Create tab object
    const tab = {
        id,
        shellId: shell,
        term: terminal,
        fitAddon,
        container,
        ws: null,
        sessionId: savedTab?.sessionId || null,  // Restore session ID if available
        heartbeatInterval: null,
        reconnectAttempts: 0,
    };

    // Handle terminal input with modifier support
    terminal.onData((data) => {
        // Clear selection when user types (Windows Terminal behavior)
        if (terminal.hasSelection()) {
            terminal.clearSelection();
        }

        let processed = data;

        // Apply modifiers to single printable characters from phone keyboard
        if (data.length === 1 && data.charCodeAt(0) >= 32 && data.charCodeAt(0) < 127) {
            const ctrlActive = modifiers.ctrl === 'sticky' || modifiers.ctrl === 'locked';
            const altActive = modifiers.alt === 'sticky' || modifiers.alt === 'locked';

            if (ctrlActive || altActive) {
                let char = data;

                if (ctrlActive) {
                    // Ctrl+letter = letter code - 64 (for uppercase)
                    const code = char.toUpperCase().charCodeAt(0);
                    if (code >= 65 && code <= 90) {
                        char = String.fromCharCode(code - 64);
                    }
                }

                if (altActive) {
                    char = '\x1b' + char;
                }

                processed = char;

                // Reset sticky modifiers after use
                if (modifiers.ctrl === 'sticky') {
                    modifiers.ctrl = 'off';
                    updateModifierButton('ctrl');
                }
                if (modifiers.alt === 'sticky') {
                    modifiers.alt = 'off';
                    updateModifierButton('alt');
                }
            }
        }

        sendInputToTab(tab, processed);
    });

    // Auto-copy on selection with debounce (for mouse users)
    // Touch selection is handled separately in gesture handler
    let selectionDebounce = null;
    terminal.onSelectionChange(() => {
        clearTimeout(selectionDebounce);
        selectionDebounce = setTimeout(() => {
            const selection = terminal.getSelection();
            if (selection && selection.length > 0) {
                navigator.clipboard.writeText(selection).catch(() => {});
            }
        }, 200);
    });

    // Handle terminal resize - use debounced handler to prevent double-firing
    terminal.onResize(() => {
        scheduleResize(tab);
    });

    tabs.push(tab);
    renderTabs();
    switchToTab(id);
    connectTab(tab, shell);

    // Save to localStorage (session ID will be saved when received from server)
    saveTabsToStorage();

    return tab;
}

/**
 * Switch to a tab
 */
function switchToTab(tabId) {
    const tab = tabs.find(t => t.id === tabId);
    if (!tab) return;

    // Hide all terminals
    tabs.forEach(t => {
        t.container.style.display = 'none';
    });

    // Show selected terminal
    tab.container.style.display = 'block';
    activeTabId = tabId;

    // Update global references
    term = tab.term;
    ws = tab.ws;
    sessionId = tab.sessionId;
    fitAddonRef = tab.fitAddon;

    // Update shell selector
    const shellSelect = document.getElementById('shell-select');
    if (shellSelect) {
        shellSelect.value = tab.shellId;
    }

    // Fit and focus
    setTimeout(() => {
        tab.fitAddon.fit();
        tab.term.focus();
    }, 50);

    renderTabs();
    saveTabsToStorage();
}

/**
 * Close a tab
 */
function closeTab(tabId) {
    const index = tabs.findIndex(t => t.id === tabId);
    if (index === -1) return;

    const tab = tabs[index];

    // Cleanup
    if (tab.ws) {
        tab.ws.close();
    }
    if (tab.heartbeatInterval) {
        clearInterval(tab.heartbeatInterval);
    }
    if (tab.inputTimeout) {
        clearTimeout(tab.inputTimeout);
    }
    tab.term.dispose();
    tab.container.remove();

    tabs.splice(index, 1);

    // Save to localStorage
    saveTabsToStorage();

    // Switch to another tab or create new one
    if (tabs.length === 0) {
        createTab();
    } else if (activeTabId === tabId) {
        switchToTab(tabs[Math.max(0, index - 1)].id);
    }

    renderTabs();
}

/**
 * Connect a tab to WebSocket
 * @param {object} tab - Tab to connect
 * @param {string|null} shellId - Shell ID to use
 * @param {boolean} skipBuffer - Skip replaying buffered output (for auto-reconnects)
 */
function connectTab(tab, shellId = null, skipBuffer = false) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let url = `${protocol}//${window.location.host}/ws`;

    const params = new URLSearchParams();
    if (tab.sessionId) {
        params.set('session_id', tab.sessionId);
    }
    if (shellId) {
        params.set('shell', shellId);
    }
    if (skipBuffer) {
        params.set('skip_buffer', '1');
    }

    if (params.toString()) {
        url += '?' + params.toString();
    }

    tab.ws = new WebSocket(url);
    tab.ws.binaryType = 'arraybuffer';

    tab.ws.onopen = () => {
        if (tab.id === activeTabId) {
            setConnectionStatus('connected');
        }
        tab.reconnectAttempts = 0;
        hideDisconnectOverlay();

        // Start heartbeat
        if (tab.heartbeatInterval) {
            clearInterval(tab.heartbeatInterval);
        }
        tab.heartbeatInterval = setInterval(() => {
            if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
                tab.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, HEARTBEAT_MS);

        // Fit terminal - onResize handler will send dimensions via scheduleResize
        try {
            tab.fitAddon.fit();
        } catch (e) {}
    };

    tab.ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
            const text = new TextDecoder().decode(event.data);
            tab.term.write(text);
        } else {
            try {
                const msg = JSON.parse(event.data);
                handleTabMessage(tab, msg);
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        }
    };

    tab.ws.onclose = (event) => {
        if (tab.id === activeTabId) {
            setConnectionStatus('disconnected');
        }

        if (tab.heartbeatInterval) {
            clearInterval(tab.heartbeatInterval);
            tab.heartbeatInterval = null;
        }

        // Handle session not found (4004) - clear session and reconnect with new one
        if (event.code === 4004) {
            console.log(`Session ${tab.sessionId} not found, creating new session`);
            tab.sessionId = null;
            saveTabsToStorage();
            tab.reconnectAttempts = 0;
            setTimeout(() => connectTab(tab, tab.shellId), 500);
            return;
        }

        // Reconnect or show disconnect overlay
        if (tab.reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            tab.reconnectAttempts++;
            const delay = RECONNECT_DELAY_MS * Math.min(tab.reconnectAttempts, 5);
            // Skip buffer replay - terminal already has the content
            setTimeout(() => connectTab(tab, null, true), delay);
        } else {
            // Max attempts reached - show disconnect overlay
            showDisconnectOverlay();
        }
    };

    tab.ws.onerror = () => {
        if (tab.id === activeTabId) {
            setConnectionStatus('disconnected');
        }
    };

    // Update global reference
    if (tab.id === activeTabId) {
        ws = tab.ws;
    }
}

/**
 * Handle messages for a tab
 */
function handleTabMessage(tab, msg) {
    switch (msg.type) {
        case 'session_info':
            tab.sessionId = msg.session_id;
            if (tab.id === activeTabId) {
                sessionId = msg.session_id;
            }
            // Save to localStorage whenever we get session info
            saveTabsToStorage();
            break;
        case 'ping':
            tab.ws.send(JSON.stringify({ type: 'pong' }));
            break;
        case 'error':
            console.error('Server error:', msg.message);
            tab.term.write(`\r\n\x1b[31mError: ${msg.message}\x1b[0m\r\n`);
            // If session not found, clear the saved session ID and retry
            if (msg.message.includes('Session not found') || msg.message.includes('unauthorized')) {
                tab.sessionId = null;
                saveTabsToStorage();
            }
            break;
    }
}

/**
 * Send input to a specific tab (immediate send for low latency)
 */
function sendInputToTab(tab, data) {
    if (!tab.ws || tab.ws.readyState !== WebSocket.OPEN) {
        return;
    }

    // Send immediately for responsive feel
    tab.ws.send(textEncoder.encode(data));
}

/**
 * Render tab bar
 */
function renderTabs() {
    const tabBar = document.getElementById('tab-bar');
    const shellSelector = document.getElementById('shell-selector');
    if (!tabBar) return;

    // Remove existing tab buttons (but keep shell-selector)
    tabBar.querySelectorAll('.tab-btn').forEach(btn => btn.remove());

    // Tab buttons (insert before shell-selector)
    tabs.forEach(tab => {
        const tabBtn = document.createElement('button');
        tabBtn.className = 'tab-btn' + (tab.id === activeTabId ? ' active' : '');

        const label = document.createElement('span');
        label.className = 'tab-label';
        label.textContent = `${tab.shellId} ${tab.id}`;
        tabBtn.appendChild(label);

        // Close button (if more than 1 tab)
        if (tabs.length > 1) {
            const closeBtn = document.createElement('span');
            closeBtn.className = 'tab-close';
            closeBtn.textContent = '×';
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeTab(tab.id);
            });
            tabBtn.appendChild(closeBtn);
        }

        tabBtn.addEventListener('click', () => switchToTab(tab.id));
        tabBar.insertBefore(tabBtn, shellSelector);
    });

    // Add tab button
    const addBtn = document.createElement('button');
    addBtn.className = 'tab-btn tab-add';
    addBtn.textContent = '+';
    addBtn.addEventListener('click', () => createTab());
    tabBar.insertBefore(addBtn, shellSelector);
}

/**
 * Initialize the application
 */
async function init() {
    // Load configuration first
    await loadConfig();

    // Setup UI
    setupButtons();
    setupModifierButtons();
    setupEscapeButton();
    setupShellSelector();
    setupGestures();
    setupDisconnectOverlay();
    setupShutdownButton();

    // Try to restore tabs from localStorage
    const stored = loadTabsFromStorage();
    if (stored && stored.tabs && stored.tabs.length > 0) {
        // Restore tabCounter
        tabCounter = stored.tabCounter || 0;

        // Restore each tab
        for (const savedTab of stored.tabs) {
            createTab(null, savedTab);
        }

        // Switch to previously active tab
        if (stored.activeTabId && tabs.find(t => t.id === stored.activeTabId)) {
            switchToTab(stored.activeTabId);
        }
    } else {
        // No saved tabs, create first tab
        createTab();
    }

    // Focus terminal on tap (double-tap now selects word via gesture handler)
    document.getElementById('terminal-container').addEventListener('click', () => {
        if (term) term.focus();
    });

    // Allow default touch behavior for text selection

    // Handle visibility change (reconnect when coming back)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            // Reconnect all disconnected tabs
            tabs.forEach(tab => {
                if (!tab.ws || tab.ws.readyState !== WebSocket.OPEN) {
                    // Skip buffer replay - terminal already has the content
                    connectTab(tab, null, true);
                }
            });
        }
    });

    // Handle window resize with debouncing (only fit active tab)
    // Note: fitAddon.fit() triggers onResize which calls scheduleResize
    let resizeDebounce;
    window.addEventListener('resize', () => {
        clearTimeout(resizeDebounce);
        resizeDebounce = setTimeout(() => {
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab) {
                activeTab.fitAddon.fit();
            }
        }, 50);
    });

    // Handle orientation change (only fit active tab)
    // Note: fitAddon.fit() triggers onResize which calls scheduleResize
    window.addEventListener('orientationchange', () => {
        setTimeout(() => {
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab) {
                activeTab.fitAddon.fit();
            }
        }, 100);
    });

    // Keep toolbar visible above keyboard on mobile
    // Note: fitAddon.fit() triggers onResize which calls scheduleResize
    if (window.visualViewport) {
        const app = document.getElementById('app');
        let viewportTimeout;
        window.visualViewport.addEventListener('resize', () => {
            app.style.height = `${window.visualViewport.height}px`;
            // Debounce refit - only fit active tab
            clearTimeout(viewportTimeout);
            viewportTimeout = setTimeout(() => {
                const activeTab = tabs.find(t => t.id === activeTabId);
                if (activeTab) {
                    activeTab.fitAddon.fit();
                }
            }, 50);
        });
    }
}

// Gesture state
let gestureState = {
    initialDistance: 0,
    initialFontSize: 14,
    isSelecting: false,
    longPressTimer: null,
    startX: 0,
    startY: 0,
    fontSizeChanged: false,
    lastTapTime: 0,
    lastTapX: 0,
    lastTapY: 0,
};

const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 24;
const LONG_PRESS_MS = 200;  // Short delay to distinguish tap from select
const DOUBLE_TAP_DISTANCE = 30;

/**
 * Convert touch coordinates to terminal buffer position
 */
function touchToTerminalPos(terminal, touch) {
    const rect = terminal.element.getBoundingClientRect();
    const x = touch.clientX - rect.left;
    const y = touch.clientY - rect.top;

    // Get cell dimensions
    const cellWidth = rect.width / terminal.cols;
    const cellHeight = rect.height / terminal.rows;

    const col = Math.floor(x / cellWidth);
    const row = Math.floor(y / cellHeight);

    return {
        col: Math.max(0, Math.min(col, terminal.cols - 1)),
        row: Math.max(0, Math.min(row, terminal.rows - 1))
    };
}

/**
 * Setup gesture controls for mobile
 * - Long-press (200ms) + drag = select text
 * - Pinch = zoom
 * - Double-tap = select word
 * - Single tap = clear selection
 */
function setupGestures() {
    const container = document.getElementById('terminal-container');
    if (!container) return;

    container.addEventListener('touchstart', (e) => {
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const touchX = touch.clientX;
            const touchY = touch.clientY;
            gestureState.startX = touchX;
            gestureState.startY = touchY;
            gestureState.isSelecting = false;

            // Start long-press timer for selection
            clearTimeout(gestureState.longPressTimer);
            gestureState.longPressTimer = setTimeout(() => {
                if (term) {
                    gestureState.isSelecting = true;
                    if (navigator.vibrate) navigator.vibrate(30);
                    // Use saved coordinates since touch object may be stale
                    const pos = touchToTerminalPos(term, { clientX: touchX, clientY: touchY });
                    const row = pos.row + term.buffer.active.viewportY;
                    term.select(pos.col, row, 1);
                }
            }, LONG_PRESS_MS);

        } else if (e.touches.length === 2) {
            clearTimeout(gestureState.longPressTimer);
            gestureState.isSelecting = false;
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            gestureState.initialDistance = Math.hypot(dx, dy);
            gestureState.initialFontSize = term ? term.options.fontSize : 14;
        }
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const dx = Math.abs(touch.clientX - gestureState.startX);
            const dy = Math.abs(touch.clientY - gestureState.startY);

            // Cancel long-press if moved before timer fires
            if (!gestureState.isSelecting && (dx > 10 || dy > 10)) {
                clearTimeout(gestureState.longPressTimer);
            }

            // Extend selection while dragging
            if (gestureState.isSelecting && term) {
                e.preventDefault();
                const startPos = touchToTerminalPos(term, { clientX: gestureState.startX, clientY: gestureState.startY });
                const endPos = touchToTerminalPos(term, touch);

                const startRow = startPos.row + term.buffer.active.viewportY;
                const endRow = endPos.row + term.buffer.active.viewportY;

                let startCol = startPos.col;
                let endCol = endPos.col;
                let length;

                if (endRow === startRow) {
                    length = Math.abs(endCol - startCol) + 1;
                    startCol = Math.min(startCol, endCol);
                } else if (endRow > startRow) {
                    length = (term.cols - startCol) + (endRow - startRow - 1) * term.cols + endCol + 1;
                } else {
                    const tmp = startRow;
                    length = (term.cols - endCol) + (tmp - endRow - 1) * term.cols + startCol + 1;
                    startCol = endCol;
                }

                term.select(startCol, Math.min(startRow, endRow), length);
            }
        } else if (e.touches.length === 2 && gestureState.initialDistance > 0) {
            const dx = e.touches[0].clientX - e.touches[1].clientX;
            const dy = e.touches[0].clientY - e.touches[1].clientY;
            const distance = Math.hypot(dx, dy);
            const scale = distance / gestureState.initialDistance;

            let newSize = Math.round(gestureState.initialFontSize * scale);
            newSize = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, newSize));

            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab && newSize !== activeTab.term.options.fontSize) {
                activeTab.term.options.fontSize = newSize;
                gestureState.fontSizeChanged = true;
            }
        }
    }, { passive: false });

    container.addEventListener('touchend', async (e) => {
        clearTimeout(gestureState.longPressTimer);

        const touch = e.changedTouches[0];
        const dx = Math.abs(touch.clientX - gestureState.startX);
        const dy = Math.abs(touch.clientY - gestureState.startY);
        const wasTap = dx < 10 && dy < 10;
        const now = Date.now();

        // Handle selection - copy to clipboard
        if (gestureState.isSelecting && term) {
            const selection = term.getSelection();
            if (selection) {
                try {
                    await navigator.clipboard.writeText(selection);
                    if (navigator.vibrate) navigator.vibrate(30);
                } catch (err) {
                    console.error('Copy failed:', err);
                }
            }
            // Clear selection after brief delay
            setTimeout(() => { if (term) term.clearSelection(); }, 300);
        }

        // Handle taps
        if (wasTap && !gestureState.isSelecting && term) {
            const tapDistance = Math.hypot(
                touch.clientX - gestureState.lastTapX,
                touch.clientY - gestureState.lastTapY
            );

            if (now - gestureState.lastTapTime < DOUBLE_TAP_MS && tapDistance < DOUBLE_TAP_DISTANCE) {
                // Double-tap: select word
                const pos = touchToTerminalPos(term, touch);
                selectWordAt(term, pos.col, pos.row + term.buffer.active.viewportY);
                gestureState.lastTapTime = 0;
            } else {
                if (term.hasSelection()) {
                    term.clearSelection();
                }
                gestureState.lastTapTime = now;
                gestureState.lastTapX = touch.clientX;
                gestureState.lastTapY = touch.clientY;
            }
        }

        // Refit after pinch-zoom
        if (gestureState.fontSizeChanged) {
            const activeTab = tabs.find(t => t.id === activeTabId);
            if (activeTab) {
                setTimeout(() => activeTab.fitAddon.fit(), 50);
            }
            gestureState.fontSizeChanged = false;
        }

        gestureState.isSelecting = false;
        gestureState.initialDistance = 0;
    }, { passive: true });

    container.addEventListener('touchcancel', () => {
        clearTimeout(gestureState.longPressTimer);
        gestureState.isSelecting = false;
        gestureState.initialDistance = 0;
    }, { passive: true });
}

/**
 * Select word at given terminal position
 */
function selectWordAt(terminal, col, row) {
    const line = terminal.buffer.active.getLine(row);
    if (!line) return;

    // Find word boundaries
    let startCol = col;
    let endCol = col;

    // Expand left
    while (startCol > 0) {
        const cell = line.getCell(startCol - 1);
        if (!cell || /\s/.test(cell.getChars())) break;
        startCol--;
    }

    // Expand right
    while (endCol < terminal.cols - 1) {
        const cell = line.getCell(endCol + 1);
        if (!cell || /\s/.test(cell.getChars())) break;
        endCol++;
    }

    const length = endCol - startCol + 1;
    if (length > 0) {
        terminal.select(startCol, row, length);
        // Copy the selected word
        const selection = terminal.getSelection();
        if (selection) {
            navigator.clipboard.writeText(selection).catch(() => {});
            if (navigator.vibrate) navigator.vibrate(30);
        }
    }
}

/**
 * Show disconnect overlay
 */
function showDisconnectOverlay() {
    const overlay = document.getElementById('disconnect-overlay');
    if (overlay) {
        overlay.classList.remove('hidden');
    }
}

/**
 * Hide disconnect overlay
 */
function hideDisconnectOverlay() {
    const overlay = document.getElementById('disconnect-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
    }
}

/**
 * Setup disconnect overlay handlers
 */
function setupDisconnectOverlay() {
    const retryBtn = document.getElementById('disconnect-retry');

    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            hideDisconnectOverlay();
            // Reset reconnect attempts and try again
            tabs.forEach(tab => {
                tab.reconnectAttempts = 0;
                if (!tab.ws || tab.ws.readyState !== WebSocket.OPEN) {
                    // Skip buffer replay - terminal already has the content
                    connectTab(tab, null, true);
                }
            });
        });
    }
}

/**
 * Setup shutdown button
 */
function setupShutdownButton() {
    const btn = document.getElementById('btn-shutdown');
    if (btn) {
        btn.addEventListener('click', async () => {
            // Require confirmation
            if (confirm('Shutdown server and tunnel?\n\nThis will terminate all sessions.')) {
                try {
                    const response = await fetch('/api/shutdown', { method: 'POST' });
                    if (response.ok) {
                        document.getElementById('disconnect-text').textContent = 'Server Shutdown';
                        showDisconnectOverlay();
                    }
                } catch (e) {
                    console.error('Shutdown failed:', e);
                }
            }
        });
    }
}

// Start the app
document.addEventListener('DOMContentLoaded', init);

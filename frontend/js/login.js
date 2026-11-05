/**
 * MULE SHIELD — Enterprise Authentication Controller
 * Manages secure login, credential verification, session storage, and route guarding.
 */

(function () {
  'use strict';

  function getApiBase() {
    if (window.location.port === '3000' || window.location.port === '8000') {
      return '';
    }
    return 'https://muleshield-vivc.onrender.com';
  }

  document.addEventListener('DOMContentLoaded', () => {
    checkExistingSession();
    checkRegistrationMessage();
    initLoginForm();
    initPasswordToggle();
    initQuickDemoFill();
    initForgotPassword();
  });

  /**
   * Displays success notification if user was redirected following account registration.
   */
  function checkRegistrationMessage() {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get('registered') === 'true') {
        const alertBox = document.getElementById('login-alert');
        const alertText = document.getElementById('login-alert-text');
        if (alertBox && alertText) {
          alertText.textContent = 'Account created successfully. Please sign in with your credentials.';
          alertBox.className = 'login-alert success';
          alertBox.style.display = 'flex';
        }
      }
    } catch (_) {}
  }

  /**
   * Automatically redirects to dashboard if user holds an active, verified token.
   */
  async function checkExistingSession() {
    const token = localStorage.getItem('mule_shield_token') || sessionStorage.getItem('mule_shield_token');
    if (!token) return;

    try {
      const res = await fetch(`${getApiBase()}/api/auth/me`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated && data.user) {
          // User already authenticated -> direct to dashboard
          window.location.replace('index.html');
        }
      } else {
        // Invalid or expired token
        localStorage.removeItem('mule_shield_token');
        localStorage.removeItem('mule_shield_user');
        sessionStorage.removeItem('mule_shield_token');
        sessionStorage.removeItem('mule_shield_user');
      }
    } catch (_) {
      // In offline/initialization state, token presence in localStorage still permits entry
      const cachedUser = localStorage.getItem('mule_shield_user');
      if (cachedUser) {
        window.location.replace('index.html');
      }
    }
  }

  /**
   * Initializes the login form submission and validation handling.
   */
  function initLoginForm() {
    const form = document.getElementById('login-form');
    const alertBox = document.getElementById('login-alert');
    const alertText = document.getElementById('login-alert-text');
    const submitBtn = document.getElementById('btn-login-submit');

    if (!form) return;

    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const identInput = document.getElementById('login-identifier');
      const passInput = document.getElementById('login-password');
      const rememberInput = document.getElementById('login-remember');

      const identifier = identInput ? identInput.value.trim() : '';
      const password = passInput ? passInput.value : '';
      const remember = rememberInput ? rememberInput.checked : true;

      // Reset alert state
      hideAlert();

      // Client pre-flight checks
      if (!identifier || !password) {
        showAlert('Please enter both your institutional email/ID and security password.', 'error');
        if (!identifier && identInput) identInput.focus();
        else if (!password && passInput) passInput.focus();
        return;
      }

      setLoading(true);

      try {
        const res = await fetch(`${getApiBase()}/api/auth/login`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            username: identifier,
            password: password,
            remember: remember
          })
        });

        const data = await res.json().catch(() => ({}));

        if (res.ok && data.status === 'success' && data.token) {
          // Store token and user metadata securely in client storage
          const storage = remember ? localStorage : sessionStorage;
          storage.setItem('mule_shield_token', data.token);
          storage.setItem('mule_shield_user', JSON.stringify(data.user));

          // Also set in localStorage for cross-tab persistence
          localStorage.setItem('mule_shield_token', data.token);
          localStorage.setItem('mule_shield_user', JSON.stringify(data.user));

          // Set secure session cookie
          const maxAge = remember ? 30 * 86400 : 86400;
          document.cookie = `mule_session=${data.token}; path=/; max-age=${maxAge}; SameSite=Lax`;

          showAlert('Clearance authenticated. Launching MULE SHIELD Command Center...', 'success');

          setTimeout(() => {
            window.location.replace('index.html');
          }, 350);
        } else {
          setLoading(false);
          const msg = data.message || 'Invalid username/email or password. Access attempt has been recorded.';
          showAlert(msg, 'error');
          if (passInput) {
            passInput.value = '';
            passInput.focus();
          }
        }
      } catch (err) {
        setLoading(false);
        showAlert('Authentication gateway unreachable. Verify backend server is running on port 8000/3000.', 'error');
      }
    });

    function showAlert(message, type) {
      if (!alertBox || !alertText) return;
      alertText.textContent = message;
      alertBox.className = `login-alert ${type}`;
      alertBox.style.display = 'flex';
    }

    function hideAlert() {
      if (!alertBox) return;
      alertBox.style.display = 'none';
    }

    function setLoading(isLoading) {
      if (!submitBtn) return;
      submitBtn.disabled = isLoading;
      const btnText = submitBtn.querySelector('.btn-text');
      const btnLoader = submitBtn.querySelector('.btn-loader');
      if (btnText) btnText.style.display = isLoading ? 'none' : 'inline-block';
      if (btnLoader) btnLoader.style.display = isLoading ? 'inline-flex' : 'none';
    }
  }

  /**
   * Password show/hide toggle behavior.
   */
  function initPasswordToggle() {
    const toggleBtn = document.getElementById('btn-toggle-password');
    const passInput = document.getElementById('login-password');
    const eyeShow = document.getElementById('eye-icon-show');
    const eyeHide = document.getElementById('eye-icon-hide');

    if (!toggleBtn || !passInput) return;

    toggleBtn.addEventListener('click', () => {
      const isPassword = passInput.getAttribute('type') === 'password';
      passInput.setAttribute('type', isPassword ? 'text' : 'password');

      if (eyeShow) eyeShow.style.display = isPassword ? 'none' : 'block';
      if (eyeHide) eyeHide.style.display = isPassword ? 'block' : 'none';
      toggleBtn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
      passInput.focus();
    });
  }

  /**
   * Fast autofill helper for the verified enterprise investigator demo account.
   */
  function initQuickDemoFill() {
    const fillBtn = document.getElementById('btn-fill-vance');
    const identInput = document.getElementById('login-identifier');
    const passInput = document.getElementById('login-password');

    if (!fillBtn || !identInput || !passInput) return;

    fillBtn.addEventListener('click', () => {
      identInput.value = 'agent.vance@muleshield.fiu.gov';
      passInput.value = 'MuleShield@2026!';
      identInput.classList.add('flash-highlight');
      passInput.classList.add('flash-highlight');
      setTimeout(() => {
        identInput.classList.remove('flash-highlight');
        passInput.classList.remove('flash-highlight');
      }, 600);
      passInput.focus();
    });
  }

  /**
   * Forgot password helper.
   */
  function initForgotPassword() {
    const link = document.getElementById('link-forgot-password');
    const alertBox = document.getElementById('login-alert');
    const alertText = document.getElementById('login-alert-text');
    const identInput = document.getElementById('login-identifier');

    if (!link) return;

    link.addEventListener('click', async (e) => {
      e.preventDefault();
      const email = identInput ? identInput.value.trim() : '';

      try {
        await fetch(`${getApiBase()}/api/auth/forgot-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: email })
        });
      } catch (_) {}

      if (alertBox && alertText) {
        alertText.textContent = 'If an authorized account is linked to your address, credential reset instructions have been forwarded to your Unit Security Officer.';
        alertBox.className = 'login-alert info';
        alertBox.style.display = 'flex';
      }
    });
  }

})();


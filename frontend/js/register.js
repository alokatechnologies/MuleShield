/**
 * MULE SHIELD — Investigator Registration Controller
 * Manages authorized analyst onboarding, cryptographic credential submission,
 * validation feedback, and redirection to the secure sign-in portal.
 */

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', () => {
    initRegisterForm();
    initPasswordToggles();
  });

  /**
   * Initializes the account registration form validation and submission.
   */
  function initRegisterForm() {
    const form = document.getElementById('register-form');
    const alertBox = document.getElementById('register-alert');
    const alertText = document.getElementById('register-alert-text');
    const submitBtn = document.getElementById('btn-register-submit');

    const nameInput = document.getElementById('reg-fullname');
    const emailInput = document.getElementById('reg-email');
    const usernameInput = document.getElementById('reg-username');
    const passInput = document.getElementById('reg-password');
    const confirmInput = document.getElementById('reg-confirm-password');

    if (!form) return;

    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const fullName = nameInput ? nameInput.value.trim() : '';
      const email = emailInput ? emailInput.value.trim() : '';
      const username = usernameInput ? usernameInput.value.trim() : '';
      const password = passInput ? passInput.value : '';
      const confirmPassword = confirmInput ? confirmInput.value : '';

      hideAlert();

      // Client pre-flight validations
      if (!fullName || !email || !username || !password || !confirmPassword) {
        showAlert('Please complete all required fields.', 'error');
        if (!fullName && nameInput) nameInput.focus();
        else if (!email && emailInput) emailInput.focus();
        else if (!username && usernameInput) usernameInput.focus();
        else if (!password && passInput) passInput.focus();
        else if (!confirmPassword && confirmInput) confirmInput.focus();
        return;
      }

      // Email format check
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(email)) {
        showAlert('Please provide a valid institutional email address.', 'error');
        if (emailInput) emailInput.focus();
        return;
      }

      // Username length check
      if (username.length < 3) {
        showAlert('Username must contain at least 3 alphanumeric characters.', 'error');
        if (usernameInput) usernameInput.focus();
        return;
      }

      // Password length check
      if (password.length < 6) {
        showAlert('Password must contain at least 6 characters.', 'error');
        if (passInput) passInput.focus();
        return;
      }

      // Password match check
      if (password !== confirmPassword) {
        showAlert('Passwords do not match. Please verify your entries.', 'error');
        if (confirmInput) {
          confirmInput.value = '';
          confirmInput.focus();
        }
        return;
      }

      setLoading(true);

      try {
        const res = await fetch('/api/auth/register', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            full_name: fullName,
            email: email,
            username: username,
            password: password,
            confirm_password: confirmPassword
          })
        });

        const data = await res.json().catch(() => ({}));

        if (res.ok && (res.status === 200 || res.status === 201)) {
          showAlert('Account created successfully! Redirecting to sign in...', 'success');
          setTimeout(() => {
            window.location.replace('login.html?registered=true');
          }, 500);
        } else {
          setLoading(false);
          const errorMsg = data.message || 'Unable to create account. Please check your details and try again.';
          showAlert(errorMsg, 'error');
        }
      } catch (err) {
        setLoading(false);
        showAlert('Registration gateway unreachable. Please verify system services are active.', 'error');
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
   * Password visibility toggles for password and confirmation fields.
   */
  function initPasswordToggles() {
    // Password toggle
    const togglePassBtn = document.getElementById('btn-toggle-reg-password');
    const passInput = document.getElementById('reg-password');
    const eyePassShow = document.getElementById('eye-icon-reg-show');
    const eyePassHide = document.getElementById('eye-icon-reg-hide');

    if (togglePassBtn && passInput) {
      togglePassBtn.addEventListener('click', () => {
        const isPassword = passInput.getAttribute('type') === 'password';
        passInput.setAttribute('type', isPassword ? 'text' : 'password');
        if (eyePassShow) eyePassShow.style.display = isPassword ? 'none' : 'block';
        if (eyePassHide) eyePassHide.style.display = isPassword ? 'block' : 'none';
        togglePassBtn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        passInput.focus();
      });
    }

    // Confirm password toggle
    const toggleConfirmBtn = document.getElementById('btn-toggle-reg-confirm');
    const confirmInput = document.getElementById('reg-confirm-password');
    const eyeConfirmShow = document.getElementById('eye-icon-confirm-show');
    const eyeConfirmHide = document.getElementById('eye-icon-confirm-hide');

    if (toggleConfirmBtn && confirmInput) {
      toggleConfirmBtn.addEventListener('click', () => {
        const isPassword = confirmInput.getAttribute('type') === 'password';
        confirmInput.setAttribute('type', isPassword ? 'text' : 'password');
        if (eyeConfirmShow) eyeConfirmShow.style.display = isPassword ? 'none' : 'block';
        if (eyeConfirmHide) eyeConfirmHide.style.display = isPassword ? 'block' : 'none';
        toggleConfirmBtn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        confirmInput.focus();
      });
    }
  }

})();


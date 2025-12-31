<%inherit file="base.mak"/>

<script>
  document.title = "Login | Stockfish Testing";
</script>

<div class="col-limited-size">
  <header class="text-md-center py-2">
    <h2>Login</h2>
    <div class="alert alert-info">
      Don't have an account?
      <strong><a href="/signup" class="alert-link">Sign up</a></strong>
    </div>
  </header>

  % if oauth_providers:
    <div class="d-grid gap-2 mb-3">
      % if 'github' in oauth_providers:
        <a class="btn btn-outline-dark" href="${oauth_providers['github']}">
          Sign in with GitHub
        </a>
      % endif
      % if 'google' in oauth_providers:
        <a class="btn btn-outline-primary" href="${oauth_providers['google']}">
          Sign in with Google
        </a>
      % endif
    </div>
    <div class="text-center text-muted my-2">or</div>
  % endif

  <form method="POST">
    <div class="form-floating mb-3">
      <input
        type="text"
        class="form-control mb-3"
        id="username"
        name="username"
        placeholder="Username"
        autocomplete="username"
        required
        autofocus
      >
      <label for="username" class="d-flex align-items-end">Username</label>
    </div>

    <div class="input-group mb-3">
      <div class="form-floating">
        <input
          type="password"
          class="form-control"
          id="password"
          name="password"
          placeholder="Password"
          autocomplete="current-password"
          required
        >
        <label for="password" class="d-flex align-items-end">Password</label>
      </div>
      <span class="input-group-text toggle-password-visibility" role="button">
        <i class="fa-solid fa-lg fa-eye pe-none" style="width: 30px"></i>
      </span>
    </div>

    <div class="mb-3 form-check">
      <label class="form-check-label" for="staylogged">Remember me</label>
      <input
        type="checkbox"
        class="form-check-input"
        id="staylogged"
        name="stay_logged_in"
      >
    </div>

    <button type="submit" class="btn btn-primary w-100">Login</button>
  </form>
</div>

<script src="${request.static_url('fishtest:static/js/toggle_password.js')}"></script>

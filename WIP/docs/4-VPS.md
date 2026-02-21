
`/etc/systemd/system/fishtest_fastapi@.services`
```ini
[Unit]
Description=Fishtest FastAPI Server port %i
After=network.target mongod.service

[Service]
Type=simple

Environment="UVICORN_WORKERS=1"
Environment="FISHTEST_URL=https://SERVER_NAME"
Environment="FISHTEST_NN_URL=https://data.stockfishchess.org"
# Cookie-session signing secret (required in production).
# Dev-only insecure fallback requires explicit opt-in: Environment="FISHTEST_INSECURE_DEV=1"
Environment="FISHTEST_AUTHENTICATION_SECRET=CHANGE_ME"
Environment="FISHTEST_CAPTCHA_SECRET="

# Port of *this* instance
Environment="FISHTEST_PORT=%i"
# Fixed primary port for the cluster
Environment="FISHTEST_PRIMARY_PORT=8000"

WorkingDirectory=/home/usr00/fishtest/server
User=usr00

ExecStart=/home/usr00/fishtest/server/.venv/bin/python -m uvicorn fishtest.app:app --host 127.0.0.1 --port %i --proxy-headers --forwarded-allow-ips=127.0.0.1 --backlog 8192 --log-level warning --workers $UVICORN_WORKERS
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

File descriptor limit override (`/etc/systemd/system/fishtest_fastapi@.service.d/override.conf`):

```ini
[Service]
LimitNOFILE=65536
```


`/etc/nginx/site-available/fistest_fastapi.com`
```nginx
upstream backend_8000 {
    server 127.0.0.1:8000;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8001 {
    server 127.0.0.1:8001;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8002 {
    server 127.0.0.1:8002;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8003 {
    server 127.0.0.1:8003;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

map $uri $backends {
    /tests                                   backend_8001;
    ~^/api/(actions|active_runs|calc_elo)    backend_8002;
    ~^/api/(nn|pgn|run_pgns)/                backend_8002;
    ~^/api/upload_pgn                        backend_8003;
    ~^/tests/(finished|machines|user)        backend_8002;
    ~^/(actions/|contributors)               backend_8002;
    ~^/(api|tests)/                          backend_8000;
    default                                  backend_8001;
}

server {
    listen 80;
    listen [::]:80;

    server_name SERVER_NAME;

    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    #listen [::]:443 ssl ipv6only=on;
    listen [::]:443 ssl;
    http2 on;

    ssl_certificate /etc/letsencrypt/live/SERVER_NAME/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/SERVER_NAME/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    server_name SERVER_NAME;

    location = /        { return 308 /tests; }
    location = /tests/  { return 308 /tests; }

    location = /nginx_status {
        # Turn on stats
        stub_status on;
    }

    location ^~ /static/ {
        alias /var/www/fishtest/static/;
        try_files $uri =404;

        access_log off;
        etag on;

        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location = /robots.txt  { return 301 /static/robots.txt; }
    location = /favicon.ico { return 301 /static/favicon.ico; }

    location /nn/ {
        root         /var/www/fishtest;
        gzip_static  always;
        gunzip       on;
    }

    location / {
        # Canonical upstream identity
        proxy_set_header Host                $http_host;
        proxy_set_header X-Real-IP           $remote_addr;

        # Forwarded chain
        proxy_set_header X-Forwarded-For     $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto   $scheme;
        proxy_set_header X-Forwarded-Host    $host;
        proxy_set_header X-Forwarded-Port    $server_port;

        # Custom metadata
        proxy_set_header X-Country-Code      $region;

        # Timeouts
        proxy_connect_timeout      2s;
        proxy_send_timeout         30s;
        proxy_read_timeout         60s;

        # Buffering
        proxy_request_buffering    on;
        proxy_buffering            on;

        client_max_body_size       200m;
        client_body_buffer_size    512k;

        proxy_redirect             off;
        proxy_http_version         1.1;

        # Decompression
        gunzip                     on;

        proxy_pass http://$backends;
    }
}
```


`fastapi_update_fishtest.sh`
```bash
#!/bin/bash
# to update a fishtest server simply run:
# sudo bash x/fastapi_update_fishtest.sh 2>&1 | tee ${HOME}/logs/fastapi_update_fishtest.log.$(date +%Y%m%d%H%M%S --utc)
#
# to use fishtest connect a browser to:
# http://<ip_address>

###echo "accidental run guard: comment to run the script" && exit

user_name='usr00'

echo "previous requirements"
# backup
sudo -i -u ${user_name} << 'EOF'
cd ${HOME}/fishtest/server
uv pip list
EOF

echo "stop fishtest"
cd /etc/nginx/sites-enabled
unlink fishtest.conf
ln -sf /etc/nginx/sites-available/fishtest-maintenance.conf fishtest-maintenance.conf
systemctl reload nginx
systemctl stop fishtest@{6543..6545}
systemctl stop fishtest_fastapi@{8000..8003}
echo "restart mongod"
systemctl restart mongod

echo "setup fishtest"
# download and prepare fishtest
sudo -i -u ${user_name} << EOF
rm -rf fishtest
git init --initial-branch=master fishtest
cd fishtest
git remote add origin https://github.com/official-stockfish/fishtest.git
git config user.email 'you@example.com'
git config user.name 'your_name'
git config core.sparseCheckout true
echo "server/" >> .git/info/sparse-checkout
git pull origin master


# add here the upstream branch to be tested
git remote add ppigazzini https://github.com/ppigazzini/fishtest
git pull --no-edit --rebase ppigazzini fastapi
#git pull --no-edit --rebase ppigazzini fastapi_server_task_duration_180

# add here the PRs to be tested
#git pull --no-edit --rebase origin pull/2430/head

#git reset --hard HEAD~1
EOF

# setup fishtest
time sudo -i -u ${user_name} << 'EOF'
cd ${HOME}/fishtest/server
uv sync
EOF

# start fishtest
systemctl start fishtest_fastapi@{8000..8003}
unlink fishtest-maintenance.conf
ln -sf /etc/nginx/sites-available/fishtest_fastapi.conf fishtest.conf
systemctl reload nginx

echo "done"
```

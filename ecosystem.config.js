module.exports = {
  apps: [
    {
      name: "ecovision-backend",
      script: "app/backend.py",
      interpreter: "python3",
      env: { APP_ENV: "production" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
    },
    {
      name: "ecovision-ai-core",
      script: "maincode/main.py",
      interpreter: "python3",
      env: { APP_ENV: "production" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
    },
    {
      name: "ecovision-frontend",
      script: "node_modules/next/dist/bin/next",
      args: "start -p 3000",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
    },
  ],
};
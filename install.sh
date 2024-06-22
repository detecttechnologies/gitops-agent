#!/bin/bash

# Define GitHub repository details
REPO="rsnk96/gitops-agent"
BRANCH="main"  # Adjust branch name as necessary

# Step 1: Install the Python package from GitHub
echo "Installing GitOps Agent from GitHub..."
python3 -m pip install --upgrade pip
pip install git+https://github.com/$REPO.git@$BRANCH

# Step 2: Download the config.toml file into /etc/gitops-agent/
echo "Setting up configuration..."
mkdir -p /etc/gitops-agent
curl -sL https://raw.githubusercontent.com/$REPO/$BRANCH/setup/templates/config.toml > /etc/gitops-agent/config.toml
chmod 666 /etc/gitops-agent/config.toml  # Readable and editable by all users

# Step 3: Create /opt/gitops-agent/ directory and set permissions
echo "Creating application directory..."
mkdir -p /opt/gitops-agent
chmod -R 777 /opt/gitops-agent  # Readable and writable by all users

# Step 4: Download the systemd service file
echo "Setting up systemd service..."
curl -sL https://raw.githubusercontent.com/$REPO/$BRANCH/setup/gitops-agent.service > /etc/systemd/system/gitops-agent.service

# Step 5: Load and start the systemd service
echo "Enabling and starting the GitOps Agent service..."
systemctl daemon-reload
systemctl enable gitops-agent.service
systemctl start gitops-agent.service

echo "GitOps Agent installation and setup complete!"

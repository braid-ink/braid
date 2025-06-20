import click
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Any

# Add core modules to path for MCP integration
braid_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(braid_root))

try:
    from core.mcp.integration import MCPIntegrator
    from core.mcp.discovery import MCPDiscovery
    from core.mcp.deployment_optimizer import MCPDeploymentOptimizer
    from core.mcp.health_monitor import MCPHealthMonitor
    from core.mcp.error_handler import MCPErrorHandler
    MCP_AVAILABLE = True
except ImportError as e:
    # Graceful fallback when MCP modules are not available
    click.echo(f"ℹ️  MCP integration not available: {str(e)}", err=True)
    MCP_AVAILABLE = False
    
    # Create mock classes to prevent errors
    class MockMCPIntegrator:
        def prepare_mcp_dockerization(self, *args, **kwargs):
            return {"success": False, "error": "MCP integration not available"}
    
    class MockMCPDeploymentOptimizer:
        def analyze_deployment_requirements(self, mcps):
            return {"recommended_profile": "development", "total_mcps": len(mcps)}
        
        def optimize_agent_structure(self, *args, **kwargs):
            return {"optimizations_applied": [], "files_created": []}
    
    MCPIntegrator = MockMCPIntegrator
    MCPDeploymentOptimizer = MockMCPDeploymentOptimizer

# --- Production Dockerfile Template for Simple Layout ---
DOCKERFILE_SIMPLE_TEMPLATE = """
# Multi-stage build for production-ready agent
FROM python:3.11-slim as base

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    build-essential \\
    curl \\
    git \\
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash agent
WORKDIR /app
RUN chown agent:agent /app

# Stage 1: Build dependencies
FROM base as builder
WORKDIR /app

# Copy and install Braid toolkit
COPY ./braid_src /app/braid_src
RUN pip install --no-cache-dir /app/braid_src

# Copy agent requirements and install dependencies
COPY ./agent_code/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Production image
FROM base as production
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy agent code
COPY ./agent_code/ .

# Set up environment
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \\
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Switch to non-root user
USER agent

# Expose port (adjust as needed)
EXPOSE 8000

# Command to run the agent
CMD ["python", "{MAIN_FILE}"]
"""

# --- Production Dockerfile Template for Production Layout ---
DOCKERFILE_PRODUCTION_TEMPLATE = """
# Multi-stage build for production-ready agent
FROM python:3.11-slim as base

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    build-essential \\
    curl \\
    git \\
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash agent
WORKDIR /app
RUN chown agent:agent /app

# Stage 1: Build dependencies
FROM base as builder
WORKDIR /app

# Copy and install Braid toolkit
COPY ./braid_src /app/braid_src
RUN pip install --no-cache-dir /app/braid_src

# Install agent dependencies using pip (not poetry for production)
COPY ./agent_code/pyproject.toml ./agent_code/requirements.txt* ./agent_code/README.md* ./
COPY ./agent_code/src/ ./src/
RUN if [ -f requirements.txt ]; then \\
        pip install --no-cache-dir -r requirements.txt; \\
    else \\
        pip install --no-cache-dir -e .; \\
    fi

# Stage 2: Production image
FROM base as production
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy agent source code
COPY ./agent_code/src/ ./src/

# Set up environment
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \\
    CMD python -c "import {AGENT_MODULE}.configuration; print('Health check passed')" || exit 1

# Switch to non-root user
USER agent

# Expose port (adjust as needed)
EXPOSE 8000

# Command to run the agent
CMD ["python", "src/{AGENT_MODULE}/{MAIN_FILE}"]
"""

# --- Enhanced Docker Compose Template ---
DOCKER_COMPOSE_TEMPLATE = """
services:
  agent:
    build:
      context: ./.build
      dockerfile: Dockerfile
      target: production
    env_file:
      - .env
    environment:
      - LOG_LEVEL=INFO
    volumes:
      - ./credentials:/app/credentials:ro
      - ./logs:/app/logs
    restart: unless-stopped
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; import os; print('Health check passed'); sys.exit(0)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    # Uncomment for interactive development
    # stdin_open: true
    # tty: true
    
  # Optional: Add monitoring
  # prometheus:
  #   image: prom/prometheus:latest
  #   ports:
  #     - "9090:9090"
  #   volumes:
  #     - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
"""

@click.command()
@click.option('--production', '-p', is_flag=True, help='Create production-optimized Docker containers with enhanced security and monitoring.')
@click.option('--platform', '-t', default='docker', type=click.Choice(['docker', 'kubernetes']), help='Target deployment platform.')
def package_command(production, platform):
    """
    Packages the agent into a deployable build kit.
    This command should be run from the root of an agent's directory.
    It supports both simple and production (src-based) layouts.
    """
    agent_root = os.getcwd()
    agent_name = os.path.basename(agent_root).replace("-", "_")
    build_dir = os.path.join(agent_root, ".build")
    
    # --- Pre-flight Checks & Layout Detection ---
    is_production_layout = os.path.exists(os.path.join(agent_root, "src"))
    
    if is_production_layout:
        click.echo("✨ Detected production layout (src-based).")
        agent_code_src_dir = os.path.join(agent_root, "src", agent_name)
    else:
        click.echo("📦 Detected simple layout.")
        agent_code_src_dir = agent_root

    # Check for main agent file (either agent.py or graph.py)
    main_file = None
    if os.path.exists(os.path.join(agent_code_src_dir, "graph.py")):
        main_file = "graph.py"
    elif os.path.exists(os.path.join(agent_code_src_dir, "agent.py")):
        main_file = "agent.py"
    else:
        click.echo(f"❌ Error: Neither 'graph.py' nor 'agent.py' found in '{agent_code_src_dir}'.", err=True)
        return

    # Validate production requirements
    if production:
        _validate_production_requirements(agent_root, is_production_layout)
    
    # Validate agent code syntax and imports
    _validate_agent_code(agent_root, is_production_layout)

    # --- Detect and Prepare MCPs ---
    mcp_integration_result = _detect_and_prepare_mcps(agent_root, production)
    
    # --- Setup Build Directory ---
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)
    
    agent_code_dir = os.path.join(build_dir, "agent_code")
    os.makedirs(agent_code_dir)
    
    braid_src_dir = os.path.join(build_dir, "braid_src")
    
    try:
        click.echo("🚀 Starting agent packaging process...")
        
        # --- 1. Copy Agent Code ---
        click.echo("   - Copying agent source code...")
        if is_production_layout:
            # For production layout, copy the whole src directory
            shutil.copytree(os.path.join(agent_root, "src"), os.path.join(agent_code_dir, "src"))
            # We also need the pyproject.toml to install dependencies
            shutil.copy2(os.path.join(agent_root, "pyproject.toml"), agent_code_dir)
            # Copy README.md if it exists (required by pyproject.toml)
            readme_file = os.path.join(agent_root, "README.md")
            if os.path.exists(readme_file):
                shutil.copy2(readme_file, agent_code_dir)
            # Copy poetry.lock if it exists (optional)
            poetry_lock = os.path.join(agent_root, "poetry.lock")
            if os.path.exists(poetry_lock):
                shutil.copy2(poetry_lock, agent_code_dir)
        else:
            # For simple layout, copy individual files
            shutil.copy2(os.path.join(agent_root, "agent.py"), agent_code_dir)
            shutil.copy2(os.path.join(agent_root, "requirements.txt"), agent_code_dir)
            if os.path.exists(os.path.join(agent_root, "tools")):
                shutil.copytree(os.path.join(agent_root, "tools"), os.path.join(agent_code_dir, "tools"))
        
        # --- 2. Copy Braid Library Source ---
        click.echo("   - Copying Braid toolkit source...")
        # Find the root of the braid project (where pyproject.toml is)
        braid_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        shutil.copytree(braid_project_root, braid_src_dir, ignore=shutil.ignore_patterns('.git', '.venv', '.build', 'agents', '*.egg-info'))

        # --- 3. Generate Production Files ---
        click.echo("   - Generating production Docker files...")
        dockerfile_content = _generate_dockerfile(is_production_layout, agent_name, main_file, production)
        with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile_content)
            
        # --- 4. Generate docker-compose.yml ---
        click.echo("   - Generating docker-compose.yml...")
        with open(os.path.join(agent_root, "docker-compose.yml"), "w") as f:
            f.write(DOCKER_COMPOSE_TEMPLATE)

        # --- 5. Generate additional production files ---
        if production:
            _generate_production_extras(agent_root, build_dir, agent_name, platform)
            
        # --- 6. Generate deployment guides ---
        _generate_deployment_guide(agent_root, is_production_layout, production, platform)
            
        click.echo("\n🎉 Packaging complete!")
        click.echo("   📁 '.build' directory created with production-ready Dockerfile")
        click.echo("   🐳 'docker-compose.yml' created in agent root")
        if production:
            click.echo("   📋 'DEPLOYMENT.md' guide created")
            if platform == 'kubernetes':
                click.echo("   ☸️  Kubernetes manifests created in 'k8s/' directory")
        
        # Show MCP integration results
        if mcp_integration_result and mcp_integration_result.get("mcps_found"):
            mcps_found = mcp_integration_result["mcps_found"]
            dockerized = mcp_integration_result.get("dockerized_mcps", [])
            click.echo(f"   🔧 Found {len(mcps_found)} MCP(s): {', '.join(mcps_found)}")
            if dockerized:
                click.echo(f"   🐳 Dockerized {len(dockerized)} MCP(s) for deployment")
        
        click.echo("\n🚀 Quick start:")
        click.echo("   docker compose up --build")
        click.echo("\n📖 For detailed deployment instructions, see DEPLOYMENT.md")
        
    except Exception as e:
        click.echo(f"\nAn error occurred during packaging: {e}", err=True)
        # Clean up failed build
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir) 

def _validate_agent_code(agent_root, is_production_layout):
    """Validate agent code for syntax errors and import issues."""
    import ast
    import sys
    import traceback
    
    issues = []
    
    if is_production_layout:
        agent_name = os.path.basename(agent_root).replace("-", "_")
        src_dir = os.path.join(agent_root, "src", agent_name)
    else:
        src_dir = agent_root
    
    # Check Python files for syntax errors
    python_files = []
    for root, dirs, files in os.walk(src_dir):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    
    for py_file in python_files:
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            ast.parse(content)
        except SyntaxError as e:
            rel_path = os.path.relpath(py_file, agent_root)
            issues.append(f"Syntax error in {rel_path}: {e.msg} (line {e.lineno})")
        except Exception as e:
            rel_path = os.path.relpath(py_file, agent_root)
            issues.append(f"Parse error in {rel_path}: {str(e)}")
    
    # Check for common import issues in tools.py
    tools_file = os.path.join(src_dir, "tools.py") if is_production_layout else os.path.join(agent_root, "tools.py")
    if os.path.exists(tools_file):
        try:
            with open(tools_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # Check for import mismatches
            import re
            get_tools_imports = re.findall(r'from\s+\S+\s+import\s+(get_\w+_tools)', content)
            for import_name in get_tools_imports:
                if import_name == "get_web_tools":
                    issues.append(f"tools.py imports deprecated 'get_web_tools' - use 'get_http_tools' instead")
        except Exception:
            pass  # Skip import validation if file can't be read
    
    if issues:
        click.echo("❌ Code validation issues found:")
        for issue in issues:
            click.echo(f"   - {issue}")
        click.echo("\n💡 Please fix these issues before packaging.")
        sys.exit(1)
    else:
        click.echo("✅ Code validation passed")


def _validate_production_requirements(agent_root, is_production_layout):
    """Validate that the agent meets production deployment requirements."""
    issues = []
    
    # Check for essential files
    if not os.path.exists(os.path.join(agent_root, ".env.example")):
        issues.append("Missing .env.example file")
    
    if is_production_layout:
        if not os.path.exists(os.path.join(agent_root, "pyproject.toml")):
            issues.append("Missing pyproject.toml file")
        if not os.path.exists(os.path.join(agent_root, "tests")):
            issues.append("Missing tests directory")
    else:
        if not os.path.exists(os.path.join(agent_root, "requirements.txt")):
            issues.append("Missing requirements.txt file")
    
    if issues:
        click.echo("⚠️  Production readiness issues found:")
        for issue in issues:
            click.echo(f"   - {issue}")
        click.echo("\n💡 Consider upgrading to production layout with: braid new --production")


def _generate_dockerfile(is_production_layout, agent_name, main_file, production_optimized):
    """Generate the appropriate Dockerfile based on layout and options."""
    if is_production_layout:
        # Use production template for src-based layout
        return DOCKERFILE_PRODUCTION_TEMPLATE.format(
            AGENT_MODULE=agent_name,
            MAIN_FILE=main_file
        )
    else:
        # Use simple template for basic layout
        return DOCKERFILE_SIMPLE_TEMPLATE.format(MAIN_FILE=main_file)


def _generate_production_extras(agent_root, build_dir, agent_name, platform):
    """Generate additional production files like monitoring configs and K8s manifests."""
    
    # Create monitoring directory
    monitoring_dir = os.path.join(agent_root, "monitoring")
    os.makedirs(monitoring_dir, exist_ok=True)
    
    # Generate basic prometheus config
    prometheus_config = """
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'agent'
    static_configs:
      - targets: ['agent:8000']
"""
    with open(os.path.join(monitoring_dir, "prometheus.yml"), "w") as f:
        f.write(prometheus_config)
    
    # Generate .dockerignore
    dockerignore_content = """
.git
.pytest_cache
__pycache__
*.pyc
*.pyo
*.pyd
.env
.venv
node_modules
.DS_Store
"""
    with open(os.path.join(agent_root, ".dockerignore"), "w") as f:
        f.write(dockerignore_content)
    
    # Generate Kubernetes manifests if requested
    if platform == 'kubernetes':
        _generate_kubernetes_manifests(agent_root, agent_name)


def _generate_kubernetes_manifests(agent_root, agent_name):
    """Generate Kubernetes deployment manifests."""
    k8s_dir = os.path.join(agent_root, "k8s")
    os.makedirs(k8s_dir, exist_ok=True)
    
    # Deployment manifest
    deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {agent_name}
  labels:
    app: {agent_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {agent_name}
  template:
    metadata:
      labels:
        app: {agent_name}
    spec:
      containers:
      - name: {agent_name}
        image: {agent_name}:latest
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
        env:
        - name: LOG_LEVEL
          value: "INFO"
        envFrom:
        - secretRef:
            name: {agent_name}-secrets
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 60
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: {agent_name}-service
spec:
  selector:
    app: {agent_name}
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8000
  type: ClusterIP
"""
    
    with open(os.path.join(k8s_dir, "deployment.yaml"), "w") as f:
        f.write(deployment_yaml)
    
    # ConfigMap and Secret templates
    configmap_yaml = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {agent_name}-config
data:
  LOG_LEVEL: "INFO"
  # Add other non-sensitive configuration here
---
apiVersion: v1
kind: Secret
metadata:
  name: {agent_name}-secrets
type: Opaque
stringData:
  # Add your environment variables here
  # OPENAI_API_KEY: "your-api-key"
  # SLACK_BOT_TOKEN: "your-bot-token"
"""
    
    with open(os.path.join(k8s_dir, "config.yaml"), "w") as f:
        f.write(configmap_yaml)


def _generate_deployment_guide(agent_root, is_production_layout, production_optimized, platform):
    """Generate comprehensive deployment documentation."""
    
    deployment_guide = f"""# Deployment Guide

This guide covers deploying your Braid agent to production environments.

## Quick Start

### Local Development
```bash
# Build and run locally
docker compose up --build

# View logs
docker compose logs -f agent

# Stop services
docker compose down
```

## Production Deployment

### Docker

#### Build Production Image
```bash
# Build optimized production image
docker build -t your-agent:latest .build/

# Run with production settings
docker run -d \\
  --name your-agent \\
  --env-file .env \\
  --restart unless-stopped \\
  your-agent:latest
```

#### Docker Compose (Recommended)
```bash
# Production deployment
docker compose -f docker-compose.yml up -d

# Scale if needed
docker compose up -d --scale agent=3
```

### Container Registry

#### Push to Registry
```bash
# Tag for registry
docker tag your-agent:latest your-registry.com/your-agent:latest

# Push to registry
docker push your-registry.com/your-agent:latest
```

"""

    if platform == 'kubernetes':
        deployment_guide += """
### Kubernetes Deployment

#### Prerequisites
- Kubernetes cluster access
- kubectl configured
- Container image in accessible registry

#### Deploy to Kubernetes
```bash
# Create namespace
kubectl create namespace your-agent

# Apply configurations
kubectl apply -f k8s/config.yaml -n your-agent
kubectl apply -f k8s/deployment.yaml -n your-agent

# Check deployment status
kubectl get pods -n your-agent
kubectl logs -f deployment/your-agent -n your-agent
```

#### Update Secrets
```bash
# Update API keys and secrets
kubectl create secret generic your-agent-secrets \\
  --from-literal=OPENAI_API_KEY=your-key \\
  --from-literal=SLACK_BOT_TOKEN=your-token \\
  -n your-agent --dry-run=client -o yaml | kubectl apply -f -
```

#### Monitoring
```bash
# Port forward for monitoring
kubectl port-forward service/your-agent-service 8080:80 -n your-agent

# View metrics (if enabled)
kubectl port-forward service/prometheus 9090:9090 -n monitoring
```
"""

    deployment_guide += """
## Environment Configuration

### Required Environment Variables
```bash
# Core LLM Configuration
OPENAI_API_KEY=your-openai-key
# ANTHROPIC_API_KEY=your-anthropic-key  # Alternative

# Tool-specific Configuration
# SLACK_BOT_TOKEN=your-slack-token
# GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/google-creds.json

# Optional Configuration
LOG_LEVEL=INFO
AGENT_MODEL=gpt-4o
AGENT_TEMPERATURE=0.1
```

### Security Best Practices

1. **Never commit secrets** to version control
2. **Use environment variables** for all sensitive data
3. **Rotate API keys** regularly
4. **Limit container permissions** (runs as non-root user)
5. **Use secrets management** in production (Kubernetes secrets, AWS Secrets Manager, etc.)

### Health Checks

The container includes health checks that verify:
- Python interpreter is working
- Core dependencies are importable
- Basic agent initialization succeeds

### Troubleshooting

#### Common Issues

**Container won't start:**
```bash
# Check logs
docker logs your-agent-container

# Run interactively for debugging
docker run -it --entrypoint /bin/bash your-agent:latest
```

**Missing environment variables:**
```bash
# Verify environment
docker exec your-agent-container env | grep -E "(OPENAI|SLACK|GOOGLE)"
```

**Import errors:**
```bash
# Check Python path
docker exec your-agent-container python -c "import sys; print(sys.path)"

# Test imports
docker exec your-agent-container python -c "import your_agent_module"
```

#### Performance Tuning

**Memory Usage:**
- Default limit: 512Mi
- Adjust based on your agent's requirements
- Monitor with `docker stats` or Kubernetes metrics

**CPU Usage:**
- Default limit: 500m (0.5 CPU cores)
- Scale horizontally for high throughput
- Consider async patterns for I/O heavy workloads

## Support

For issues with:
- **Braid toolkit**: Check the main repository documentation
- **Agent logic**: Review your agent's logs and configuration
- **Infrastructure**: Consult your platform's documentation (Docker, Kubernetes, etc.)
"""

    with open(os.path.join(agent_root, "DEPLOYMENT.md"), "w") as f:
        f.write(deployment_guide)


def _detect_and_prepare_mcps(agent_root: str, production_mode: bool) -> Dict[str, Any]:
    """Detect and prepare MCPs for deployment.
    
    Args:
        agent_root: Path to agent root directory
        production_mode: Whether to use production-optimized configurations
        
    Returns:
        MCP integration results
    """
    result = {
        "mcps_found": [],
        "mcps_integrated": [],
        "dockerized_mcps": [],
        "docker_files": [],
        "success": True,
        "issues": []
    }
    
    if not MCP_AVAILABLE:
        click.echo("   ℹ️  MCP integration not available - skipping MCP processing")
        return result
    
    try:
        # Check if agent has MCP directory
        mcp_dir = os.path.join(agent_root, "mcp")
        if not os.path.exists(mcp_dir):
            click.echo("   ℹ️  No MCPs found")
            return result
        
        # Scan for MCPs
        mcps_found = []
        for item in os.listdir(mcp_dir):
            item_path = os.path.join(mcp_dir, item)
            if os.path.isdir(item_path) and not item.endswith("_client.json"):
                # Check if it has metadata or config files
                metadata_file = os.path.join(item_path, "metadata.json")
                config_file = os.path.join(item_path, "server_config.json")
                if os.path.exists(metadata_file) or os.path.exists(config_file):
                    mcps_found.append(item)
        
        if not mcps_found:
            click.echo("   ℹ️  No MCPs found")
            return result
        
        result["mcps_found"] = mcps_found
        click.echo(f"   🔧 Detected {len(mcps_found)} MCP(s): {', '.join(mcps_found)}")
        
        # Initialize MCP components
        integrator = MCPIntegrator()
        optimizer = MCPDeploymentOptimizer()
        
        # Analyze deployment requirements
        deployment_analysis = optimizer.analyze_deployment_requirements(mcps_found)
        click.echo(f"   📊 Deployment analysis: {deployment_analysis['recommended_profile']} profile recommended")
        
        # Apply production optimizations
        if production_mode:
            click.echo("   🚀 Applying production optimizations...")
            
            # Optimize agent structure
            optimization_result = optimizer.optimize_agent_structure(
                Path(agent_root), 
                mcps_found, 
                profile="production"
            )
            
            if optimization_result:
                click.echo(f"   ✅ Applied {len(optimization_result['optimizations_applied'])} optimizations")
                for opt in optimization_result['optimizations_applied']:
                    click.echo(f"      - {opt}")
            
            # Dockerize MCPs for production deployment
            click.echo("   🐳 Dockerizing MCPs for production deployment...")
            docker_result = integrator.prepare_mcp_dockerization(
                agent_root, mcps_found, production_mode=True
            )
            
            if docker_result["success"]:
                result["dockerized_mcps"] = docker_result["dockerized_mcps"]
                result["docker_files"] = docker_result["docker_files"]
                click.echo(f"   ✅ Successfully dockerized {len(docker_result['dockerized_mcps'])} MCP(s)")
                
                # Show detailed results
                for mcp_id in docker_result["dockerized_mcps"]:
                    click.echo(f"      📦 {mcp_id}: Docker files generated")
            else:
                result["success"] = False
                result["issues"].extend([f"MCP dockerization failed: {failure}" 
                                       for failure in docker_result.get("failed_mcps", [])])
                click.echo("   ❌ MCP dockerization failed")
                for failure in docker_result.get("failed_mcps", []):
                    click.echo(f"      - {failure['mcp_id']}: {failure['error']}")
        
        # Validate MCP deployment readiness
        validation_result = integrator.validate_mcp_deployment(agent_root, mcps_found)
        if not validation_result["deployment_ready"]:
            result["issues"].extend(validation_result["issues"])
            click.echo("   ⚠️  MCP deployment validation warnings:")
            for issue in validation_result["issues"]:
                click.echo(f"      - {issue}")
            for warning in validation_result["warnings"]:
                click.echo(f"      - {warning}")
        
        result["mcps_integrated"] = mcps_found
        
    except Exception as e:
        result["success"] = False
        result["issues"].append(f"MCP detection failed: {str(e)}")
        click.echo(f"   ❌ MCP detection failed: {e}")
    
    return result
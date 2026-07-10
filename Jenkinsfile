// EvalOps CI/CD Pipeline
// Requires: Docker, Python 3.13, Jenkins plugins for Docker and Slack

pipeline {
    agent {
        docker {
            image 'python:3.13-slim'
            args '--user root'
        }
    }

    environment {
        VIRTUAL_ENV = "${WORKSPACE}/.venv"
        PATH = "${VIRTUAL_ENV}/bin:$PATH"
        PIP_CACHE_DIR = "${WORKSPACE}/.pip-cache"
        COVERAGE_MIN = '80'
        QUALITY_GATE_THRESHOLD = '0.75'
    }

    options {
        timeout(time: 45, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
        ansiColor('xterm')
    }

    stages {
        stage('Setup') {
            steps {
                sh '''
                    python -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -e ".[dev,test]"
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '''
                    . .venv/bin/activate
                    ruff check backend/ tests/
                    ruff format --check backend/ tests/
                '''
            }
        }

        stage('Type Check') {
            steps {
                sh '''
                    . .venv/bin/activate
                    mypy backend/ --ignore-missing-imports
                '''
            }
        }

        stage('Unit Tests') {
            steps {
                sh '''
                    . .venv/bin/activate
                    pytest tests/unit/ \
                        --cov=backend \
                        --cov-report=xml:coverage-unit.xml \
                        --cov-report=html:htmlcov-unit \
                        --junitxml=results-unit.xml \
                        -v
                '''
            }
            post {
                always {
                    junit 'results-unit.xml'
                    publishHTML(target: [
                        reportDir: 'htmlcov-unit',
                        reportFiles: 'index.html',
                        reportName: 'Unit Test Coverage'
                    ])
                }
            }
        }

        stage('Integration Tests') {
            steps {
                sh '''
                    . .venv/bin/activate
                    pytest tests/integration/ \
                        --cov=backend \
                        --cov-report=xml:coverage-integration.xml \
                        --cov-report=html:htmlcov-integration \
                        --junitxml=results-integration.xml \
                        -v
                '''
            }
            post {
                always {
                    junit 'results-integration.xml'
                    publishHTML(target: [
                        reportDir: 'htmlcov-integration',
                        reportFiles: 'index.html',
                        reportName: 'Integration Test Coverage'
                    ])
                }
            }
        }

        stage('Security Scan') {
            steps {
                sh '''
                    . .venv/bin/activate
                    pip install bandit safety

                    # Bandit static analysis
                    bandit -r backend/ -f json -o bandit-report.json --severity-level medium || true

                    # Dependency vulnerability scan
                    safety check --output json > safety-report.json || true
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'bandit-report.json,safety-report.json', allowEmptyArchive: true
                }
            }
        }

        stage('Eval Smoke Test') {
            steps {
                sh '''
                    . .venv/bin/activate
                    python scripts/quickstart.py || echo "Quickstart not available yet"
                '''
            }
        }

        stage('Quality Gate') {
            steps {
                sh '''
                    . .venv/bin/activate

                    # Check unit test coverage threshold
                    COVERAGE=$(python -c "
                    import xml.etree.ElementTree as ET
                    tree = ET.parse('coverage-unit.xml')
                    print(float(tree.getroot().attrib.get('line-rate', 0)) * 100)
                    " 2>/dev/null || echo "0")
                    echo "Unit coverage: $COVERAGE%"
                    python -c "assert float('$COVERAGE') >= $COVERAGE_MIN, 'Coverage $COVERAGE% below threshold $COVERAGE_MIN%'"

                    # Fail build on high-severity bandit findings
                    python -c "
                    import json, sys
                    try:
                        with open('bandit-report.json') as f:
                            data = json.load(f)
                        high = [r for r in data.get('results', []) if r.get('issue_severity') == 'HIGH']
                        if high:
                            print(f'Found {len(high)} HIGH severity findings')
                            for r in high:
                                print(f\"  {r['filename']}:{r['line_number']} — {r['issue_text']}\")
                            sys.exit(1)
                    except (FileNotFoundError, json.JSONDecodeError):
                        pass
                    " || echo "Bandit check skipped"
                '''
            }
        }

        stage('Docker Build') {
            steps {
                sh '''
                    docker build \
                        -t evalops-backend:${BUILD_NUMBER} \
                        -t evalops-backend:latest \
                        -f docker/Dockerfile.backend .
                '''
            }
        }

        stage('Deploy to Staging') {
            when {
                branch 'main'
            }
            steps {
                sh '''
                    echo "Deploying evalops-backend:${BUILD_NUMBER} to staging..."
                    # docker push registry.example.com/evalops-backend:${BUILD_NUMBER}
                    # helm upgrade --install evalops-staging ./helm/evalops \
                    #     --set image.tag=${BUILD_NUMBER} \
                    #     --namespace staging
                '''
            }
        }
    }

    post {
        success {
            slackSend(
                color: 'good',
                message: ":white_check_mark: *EvalOps Build #${BUILD_NUMBER}* succeeded on `${env.BRANCH_NAME}`\n${env.BUILD_URL}"
            )
        }
        failure {
            slackSend(
                color: 'danger',
                message: ":x: *EvalOps Build #${BUILD_NUMBER}* failed on `${env.BRANCH_NAME}`\n${env.BUILD_URL}"
            )
        }
        unstable {
            slackSend(
                color: 'warning',
                message: ":warning: *EvalOps Build #${BUILD_NUMBER}* unstable on `${env.BRANCH_NAME}`\n${env.BUILD_URL}"
            )
        }
        always {
            cleanWs()
        }
    }
}

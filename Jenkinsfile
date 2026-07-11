// EvalOps CI/CD Pipeline
// Runs on a Jenkins node (agent any) that has the following tools installed:
//   - Python 3.13 (python3)          - Node.js 20 (node/npm)
//   - Docker + Docker Compose v2      - git, curl
//
// Behaviour:
//   * Every branch / PR : install -> lint -> type-check -> test -> build images
//   * main / master     : additionally deploys via docker compose (regular deployments)
//
// Optional Jenkins plugins: JUnit (test reports), Slack Notification (alerts).
// Slack alerts are guarded so the build still passes if the plugin is absent.

pipeline {
    agent any

    environment {
        PIP_CACHE_DIR   = "${WORKSPACE}/.pip-cache"
        VENV            = "${WORKSPACE}/.venv"
        COMPOSE_FILE    = "docker/docker-compose.yml"
        BACKEND_IMAGE   = "evalops-backend"
        FRONTEND_IMAGE  = "evalops-frontend"
        IMAGE_TAG       = "${env.BUILD_NUMBER}"
        COVERAGE_MIN    = "70"
    }

    options {
        timeout(time: 45, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    stages {
        stage('Preflight') {
            steps {
                sh '''
                    set -e
                    echo "Verifying required tooling..."
                    python3 --version
                    node --version
                    npm --version
                    docker --version
                    docker compose version
                '''
            }
        }

        stage('Backend: Setup') {
            steps {
                sh '''
                    set -e
                    python3 -m venv "$VENV"
                    . "$VENV/bin/activate"
                    pip install --upgrade pip
                    pip install -e ".[dev,test]"
                '''
            }
        }

        stage('Frontend: Install') {
            steps {
                dir('frontend') {
                    sh 'npm ci'
                }
            }
        }

        stage('Quality Checks') {
            parallel {
                stage('Backend Lint') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh '''
                                . "$VENV/bin/activate"
                                ruff check backend/ tests/
                            '''
                        }
                    }
                }
                stage('Backend Type Check') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh '''
                                . "$VENV/bin/activate"
                                mypy backend/ --ignore-missing-imports
                            '''
                        }
                    }
                }
                stage('Frontend Lint') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            dir('frontend') {
                                sh 'npm run lint'
                            }
                        }
                    }
                }
                stage('Frontend Type Check') {
                    steps {
                        dir('frontend') {
                            sh 'npx tsc --noEmit'
                        }
                    }
                }
            }
        }

        stage('Backend: Tests') {
            steps {
                sh '''
                    set -e
                    . "$VENV/bin/activate"
                    pytest tests/unit/ tests/integration/ \
                        --cov=backend \
                        --cov-report=xml:coverage.xml \
                        --cov-report=term-missing \
                        --junitxml=results.xml \
                        -q
                '''
            }
            post {
                always {
                    junit testResults: 'results.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'coverage.xml', allowEmptyArchive: true
                }
            }
        }

        stage('Frontend: Build') {
            steps {
                dir('frontend') {
                    sh 'npm run build'
                }
            }
        }

        stage('Security Scan') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '''
                        . "$VENV/bin/activate"
                        pip install bandit
                        bandit -r backend/ -f json -o bandit-report.json --severity-level medium || true
                    '''
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'bandit-report.json', allowEmptyArchive: true
                }
            }
        }

        stage('Docker Build') {
            steps {
                sh '''
                    set -e
                    docker build \
                        -t ${BACKEND_IMAGE}:${IMAGE_TAG} \
                        -t ${BACKEND_IMAGE}:latest \
                        -f docker/Dockerfile.backend .

                    docker build \
                        -t ${FRONTEND_IMAGE}:${IMAGE_TAG} \
                        -t ${FRONTEND_IMAGE}:latest \
                        -f frontend/Dockerfile frontend
                '''
            }
        }

        stage('Deploy') {
            when {
                anyOf {
                    branch 'main'
                    branch 'master'
                }
            }
            steps {
                sh '''
                    set -e
                    echo "Deploying EvalOps stack (build ${IMAGE_TAG})..."
                    docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans
                    docker compose -f "$COMPOSE_FILE" ps
                '''
            }
        }

        stage('Smoke Test') {
            when {
                anyOf {
                    branch 'main'
                    branch 'master'
                }
            }
            steps {
                sh '''
                    set -e
                    echo "Waiting for backend health..."
                    for i in $(seq 1 30); do
                        if curl -fs http://localhost:8000/health > /dev/null; then
                            echo "Backend healthy."
                            exit 0
                        fi
                        sleep 5
                    done
                    echo "Backend did not become healthy in time." >&2
                    docker compose -f "$COMPOSE_FILE" logs backend | tail -n 50 >&2
                    exit 1
                '''
            }
        }
    }

    post {
        success {
            script {
                try {
                    slackSend(color: 'good',
                        message: ":white_check_mark: EvalOps #${BUILD_NUMBER} succeeded on ${env.BRANCH_NAME}\n${env.BUILD_URL}")
                } catch (ignored) {
                    echo "Slack notification skipped (plugin not configured)."
                }
            }
        }
        failure {
            script {
                try {
                    slackSend(color: 'danger',
                        message: ":x: EvalOps #${BUILD_NUMBER} failed on ${env.BRANCH_NAME}\n${env.BUILD_URL}")
                } catch (ignored) {
                    echo "Slack notification skipped (plugin not configured)."
                }
            }
        }
        always {
            cleanWs()
        }
    }
}

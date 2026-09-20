variable "PPAP_BUILD_SHA" {
  default = "unknown"
}

group "default" {
  targets = ["api", "worker", "web"]
}

target "api" {
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
    PPAP_SERVICE   = "api"
  }
  tags = ["ppap-api:${PPAP_BUILD_SHA}"]
}

target "worker" {
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
    PPAP_SERVICE   = "worker"
  }
  tags = ["ppap-worker:${PPAP_BUILD_SHA}"]
}

target "web" {
  context    = "."
  dockerfile = "deploy/web/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
  }
  tags = ["ppap-web:${PPAP_BUILD_SHA}"]
}

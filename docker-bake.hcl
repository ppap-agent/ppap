variable "PPAP_BUILD_SHA" {
  default = "unknown"
}

variable "PPAP_IMAGE_PREFIX" {
  default = "ppap-"
}

group "default" {
  targets = ["api", "worker", "web"]
}

group "release" {
  targets = ["api", "worker", "web", "deep"]
}

target "_metadata" {
  labels = {
    "org.opencontainers.image.source"   = "https://github.com/ppap-agent/ppap"
    "org.opencontainers.image.revision" = PPAP_BUILD_SHA
  }
}

target "api" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
    PPAP_SERVICE   = "api"
  }
  tags = ["${PPAP_IMAGE_PREFIX}api:${PPAP_BUILD_SHA}"]
}

target "worker" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
    PPAP_SERVICE   = "worker"
  }
  tags = ["${PPAP_IMAGE_PREFIX}worker:${PPAP_BUILD_SHA}"]
}

target "web" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/web/Dockerfile"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
  }
  tags = ["${PPAP_IMAGE_PREFIX}web:${PPAP_BUILD_SHA}"]
}

target "deep" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/deep/Dockerfile"
  contexts = {
    ppap-worker = "target:worker"
  }
  args = {
    PPAP_APP_IMAGE = "ppap-worker"
  }
  tags = ["${PPAP_IMAGE_PREFIX}deep:${PPAP_BUILD_SHA}"]
}

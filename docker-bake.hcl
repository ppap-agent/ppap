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
  target     = "app"
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
  target     = "app"
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

# Deep is the Worker application with Pi, from the same Dockerfile and source. Its Pi layers sit
# below the source (deep-base, one of the base levels in deploy/tke/bases.py), so a release that
# changes only source re-pushes only source layers.
target "deep" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  target     = "deep"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
    PPAP_SERVICE   = "worker"
    PPAP_APP_BASE  = "deep-base"
  }
  tags = ["${PPAP_IMAGE_PREFIX}deep:${PPAP_BUILD_SHA}"]
}

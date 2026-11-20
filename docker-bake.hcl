variable "PPAP_BUILD_SHA" {
  default = "unknown"
}

variable "PPAP_IMAGE_PREFIX" {
  default = "ppap-"
}

# Tag of the shared dependency images: the digest printed by deploy/tke/base-inputs.py.
variable "PPAP_BASE_TAG" {
  default = "local"
}

group "default" {
  targets = ["api", "worker", "web"]
}

group "release" {
  targets = ["api", "worker", "web", "deep"]
}

# The dependency layers, built only when their inputs change (deploy/tke/base-images.json). Releases
# then build source layers on these images instead of rebuilding and re-pushing hundreds of MB.
group "bases" {
  targets = ["deps", "deep-base"]
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
# below the source (deep-base), so a release that changes only source re-pushes only source layers.
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

target "deps" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  target     = "deps"
  tags       = ["${PPAP_IMAGE_PREFIX}deps:${PPAP_BASE_TAG}"]
}

target "deep-base" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  target     = "deep-base"
  tags       = ["${PPAP_IMAGE_PREFIX}deep-base:${PPAP_BASE_TAG}"]
}

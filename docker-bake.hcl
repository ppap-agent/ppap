variable "PPAP_BUILD_SHA" {
  default = "unknown"
}

variable "PPAP_IMAGE_PREFIX" {
  default = "ppap-"
}

group "default" {
  targets = ["app", "web"]
}

group "release" {
  targets = ["app", "web", "deep"]
}

target "_metadata" {
  labels = {
    "org.opencontainers.image.source"   = "https://github.com/ppap-agent/ppap"
    "org.opencontainers.image.revision" = PPAP_BUILD_SHA
  }
}

# API and Worker run the same image; each deployment selects its process with PPAP_SERVICE.
target "app" {
  inherits   = ["_metadata"]
  context    = "."
  dockerfile = "deploy/app/Dockerfile"
  target     = "app"
  args = {
    PPAP_BUILD_SHA = PPAP_BUILD_SHA
  }
  tags = ["${PPAP_IMAGE_PREFIX}app:${PPAP_BUILD_SHA}"]
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

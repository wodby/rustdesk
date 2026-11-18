-include env_make

RUSTDESK_VER ?= 1.1.16
RUSTDESK_VER_MINOR = $(shell echo "${RUSTDESK_VER}" | grep -oE '^[0-9]+\.[0-9]+')

TAG ?= $(RUSTDESK_VER_MINOR)

REPO = wodby/rustdesk
NAME = rustdesk-$(RUSTDESK_VER)

IMAGETOOLS_TAG ?= $(TAG)

ifneq ($(ARCH),)
	override TAG := $(TAG)-$(ARCH)
endif

.PHONY: build test push shell run clean buildx-imagetools-create

default: build

build:
	docker build -t $(REPO):$(TAG) --build-arg RUSTDESK_VER=$(RUSTDESK_VER) ./

test:
	cd ./tests && IMAGE=$(REPO):$(TAG) NAME=$(NAME) RUSTDESK_VER=$(RUSTDESK_VER) ./run.sh

push:
	docker push $(REPO):$(TAG)

# CI combines the exact architecture images this workflow run pushed; see
# wodby/images arch-image-pins. Per-architecture tags are shared by every run,
# so an overlapping run may already have replaced one of them.
arch-image = $(if $(ARCH_IMAGE_PINS),$(or $(shell cat '$(ARCH_IMAGE_PINS)/$(subst :,_,$(subst /,_,$(1)))' 2>/dev/null),$(error No digest of $(1) was pushed by this workflow run)),$(1))

buildx-imagetools-create:
	docker buildx imagetools create -t $(REPO):$(IMAGETOOLS_TAG) \
				$(call arch-image,$(REPO):$(TAG)-amd64) \
				$(call arch-image,$(REPO):$(TAG)-arm64)

shell:
	docker run --rm --name $(NAME) -i -t $(PORTS) $(VOLUMES) $(ENV) $(REPO):$(TAG) /bin/bash

run:
	docker run --rm --name $(NAME) -p 8080:8080 $(PORTS) $(VOLUMES) $(ENV) $(REPO):$(TAG) $(CMD)

clean:
	-docker rm -f $(NAME)

# Print the image make builds and pushes; CI pushes exactly this reference.
.PHONY: image-ref
image-ref:
	@printf '%s\n' '$(REPO):$(TAG)'

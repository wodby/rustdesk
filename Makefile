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

buildx-imagetools-create:
	docker buildx imagetools create -t $(REPO):$(IMAGETOOLS_TAG) \
				$(REPO):$(TAG)-amd64 \
				$(REPO):$(TAG)-arm64

shell:
	docker run --rm --name $(NAME) -i -t $(PORTS) $(VOLUMES) $(ENV) $(REPO):$(TAG) /bin/bash

run:
	docker run --rm --name $(NAME) -p 8080:8080 $(PORTS) $(VOLUMES) $(ENV) $(REPO):$(TAG) $(CMD)

clean:
	-docker rm -f $(NAME)

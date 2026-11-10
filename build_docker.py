#!/usr/bin/env python3
import argparse
import datetime
import logging
import os
import subprocess

DEFAULT_USERNAME = "valyriantech"
today_tag = datetime.datetime.now(datetime.UTC).strftime("%d%m%Y")

logger = logging.getLogger()
logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)

project_dir = os.path.dirname(os.path.realpath(__file__))
username = DEFAULT_USERNAME


def docker_command(command):
    try:
        logger.info(f"Running docker command: {command}")
        subprocess.check_call(command)
    except subprocess.CalledProcessError as e:
        logger.error(f"Got error while executing docker command: {e}")
        raise


def build(docker_repo, tag, from_docker=None):
    docker_container = f"{username}/{docker_repo}:{tag}"
    logger.info(f"Building and pushing {docker_container}")

    build_command = ["docker", "build", "--progress=plain", "-t", docker_container]
    if from_docker is not None:
        build_command += ["--build-arg", f"DOCKER_FROM={from_docker}"]
    build_command += [project_dir]

    push_command = ["docker", "push", docker_container]

    docker_command(build_command)
    docker_command(push_command)

    return docker_container


def tag(source_container, target_container):
    docker_command(["docker", "tag", source_container, target_container])
    docker_command(["docker", "push", target_container])


def main(argv=None):
    global username

    parser = argparse.ArgumentParser(description="Build Dockerfile")
    parser.add_argument("docker", type=str, help="Name of the Docker image to build")
    parser.add_argument("--username", type=str, default=DEFAULT_USERNAME, help="Username to build/push under")
    parser.add_argument("--tag", type=str, default=today_tag, help=f"Tag to use. Defaults to today's date: {today_tag}")
    parser.add_argument("--latest", action="store_true", help="If specified, we will also tag and push :latest")
    args = parser.parse_args(argv)

    username = args.username

    container = build(args.docker, args.tag)
    logger.info(f"Successfully built and pushed the container to {container}")

    if args.latest:
        latest = f"{username}/{args.docker}:latest"
        tag(container, latest)
        logger.info(f"Successfully tagged and pushed to {latest}")


if __name__ == "__main__":
    main()

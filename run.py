import os
import sys
import tarfile
import tempfile
import time
import yaml
import atexit

import grpc
import src.compose_adapter.grpc_connector.client_pb2_grpc as client_pb2_grpc
from concurrent import futures

import src.compose_adapter.grpc_connector.client_pb2 as client_pb2
from src.compose_adapter.utils import epm_utils as epm_utils
from src.compose_adapter.handlers import compose_handler, docker_handler
from src.compose_adapter.utils import utils

_ONE_DAY_IN_SECONDS = 60 * 60 * 24


class ComposeHandlerService(client_pb2_grpc.OperationHandlerServicer):
    def Create(self, request, context):

        default_packages_path = os.path.dirname(__file__) + "/packages"
        if not os.path.exists(default_packages_path):
            os.mkdir(default_packages_path)

        temp = tempfile.NamedTemporaryFile(delete=True)
        temp.write(request.file)
        package = tarfile.open(temp.name, "r")

        metadata = utils.extract_metadata(package)
        if metadata is None:
            raise Exception("No metadata found in package!")
        package_name = metadata.get("name")

        registry_credentials = []
        if "docker_registry" in metadata:
            if "docker_username" in metadata:
                password = ""
                if "docker_password" in metadata:
                    password = metadata.get("docker_password")
                registry_credentials.append(metadata.get("docker_registry"))
                registry_credentials.append(metadata.get("docker_username"))
                registry_credentials.append(password)
            else:
                print("If you specify a custom docker registry, you need to specify the login credentials.")

        compose_path = os.path.dirname(__file__) + "/packages/" + package_name
        if not os.path.exists(compose_path):
            os.mkdir(compose_path)

        compose = utils.extract_compose(package)
        if compose is None:
            raise Exception("No docker-compose file found in package!")
        f = open(compose_path + "/docker-compose.yml", "wb")
        f.write(compose)
        f.close()
        package.close()
        temp.close()

        options = request.options
        if len(options) >= 2:
            enabled = options[0] == "True"
            address = str(options[1])
        else:
            enabled=False
            address=""

        if len(registry_credentials) == 3:
            docker_handler.login_to_registry(registry_credentials)

        container_ids = compose_handler.up(project_path=compose_path, default_logging=enabled, logging_address=address)
        rg = docker_handler.convert_to_resource_group(container_ids, resource_group_name=package_name)
        return rg

    def CheckStatus(self, request, context):
        """
        Status:
        0 - CONFIGURING
        1 - ACTIVE
        2 - INACTIVE
        """
        return client_pb2.Status(status=1)

    def CheckIfContainerExists(self, request, context):

        compose_id = request.resource_id
        result = str(docker_handler.check_container_exists(compose_id, True))
        return client_pb2.StringResponse(response=result)

    def CheckIfContainerRunning(self, request, context):

        compose_id = request.resource_id
        result = str(docker_handler.check_container_exists(compose_id, False))
        return client_pb2.StringResponse(response=result)

    def Remove(self, request, context):

        compose_id = request.resource_id
        compose_path = os.path.dirname(__file__) + "/packages/" + compose_id
        compose_handler.rm(project_path=compose_path)

        os.remove(compose_path + "/docker-compose.yml")
        os.rmdir(compose_path)

        return client_pb2.Empty()

    def StartContainer(self, request, context):
        container_id = request.resource_id
        print("Starting container " + container_id)
        docker_handler.start_container(container_id)
        return client_pb2.Empty()

    def StopContainer(self, request, context):
        container_id = request.resource_id
        print("Stopping container " + container_id)
        docker_handler.stop_container(container_id)
        return client_pb2.Empty()

    def ExecuteCommand(self, request, context):
        container_id = request.resource_id
        command = request.property[0]
        print("Executing command " + command)
        output = docker_handler.execute_on_container(container_id, command)
        return client_pb2.StringResponse(response=output)

    def DownloadFile(self, request, context):
        container_id = request.resource_id
        path = request.property[0]
        print("Downloading file " + path)
        output = docker_handler.download_file_from_container(container_id, path)
        return client_pb2.FileMessage(file=output)

    def UploadFile(self, request, context):

        container_id = request.resource_id
        type = request.property[0]
        if type == "withPath":
            remotePath = request.property[2]
            hostPath = request.property[1]
            print("Uploading a file " + hostPath + " to " + remotePath)
            docker_handler.upload_file_to_container_from_path(container_id, hostPath, remotePath)
            return client_pb2.Empty()
        else:
            path = request.property[0]
            print("Uploading a file to " + path)
            file = request.file
            docker_handler.upload_file_to_container(container_id, path, file)
            return client_pb2.Empty()



def serve(port="50051"):
    print("Starting server...")
    print("Listening on port: " + port)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    client_pb2_grpc.add_OperationHandlerServicer_to_server(
        ComposeHandlerService(), server)
    server.add_insecure_port('[::]:' + port)
    server.start()
    try:
        while True:
            time.sleep(_ONE_DAY_IN_SECONDS)
    except KeyboardInterrupt:
        server.stop(0)

adapter_id = ""
epm_ip = ""

@atexit.register
def stop():
    print("Exiting")
    if adapter_id != "" and epm_ip != "":
        print("DELETING ADAPTER")
        epm_utils.unregister_adapter(epm_ip, adapter_id)


if __name__ == '__main__':
    if "--register-adapter" in sys.argv:
        if len(sys.argv) == 4:
            print("Trying to register pop to EPM container...")
            adapter_id = epm_utils.register_adapter(ip=sys.argv[2], compose_ip=sys.argv[3])
            epm_ip = sys.argv[2]
        else:
            ip = "elastest-epm"
            compose_ip = "elastest-epm-adapter-docker-compose"
            adapter_id = epm_utils.register_adapter(ip=ip, compose_ip=compose_ip)
            epm_ip = ip
    serve()

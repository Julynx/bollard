from bollard import DockerClient


def test_network_lifecycle(docker_client: DockerClient, random_name: str) -> None:
    """Test create, inspect, remove network."""
    network_name = f"{random_name}-net"

    try:
        network = docker_client.create_network(network_name, driver="bridge")
        assert network.resource_id is not None

        info = network.inspect()
        assert info["Name"] == network_name

    finally:
        try:
            if "network" in locals():
                network.remove()
        except Exception:
            pass

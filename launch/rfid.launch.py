"""Standalone RFID launch — driver + bridge.

Makes rfid_node individually usable:
    ros2 launch rfid_node rfid.launch.py
    ros2 launch rfid_node rfid.launch.py broker_type:=hivemq

Broker connectivity is non-blocking; with no broker reachable the device is
still fully controllable over ROS (rfid/cmd, rfid/mode).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch_ros.actions import Node


def _setup(context, *args, **kwargs):
    broker_override = context.launch_configurations.get("broker_type", "").strip()

    from rfid_node.config_loader import load_config
    cfg = load_config()
    if broker_override == "hivemq":
        broker = cfg.hivemq_broker
    elif broker_override == "local":
        broker = cfg.local_broker
    else:
        broker = cfg.active_broker()

    bridge_params = {
        "mqtt_host": broker.host,
        "mqtt_port": broker.port,
        "use_tls":   broker.use_tls,
    }
    if broker.username:
        bridge_params["mqtt_username"] = broker.username
        bridge_params["mqtt_password"] = broker.password

    return [
        Node(package="rfid_node", executable="rfid_driver_node", output="screen"),
        Node(package="rfid_node", executable="rfid_bridge_node", output="screen",
             parameters=[bridge_params]),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "broker_type", default_value="",
            description='MQTT broker: hivemq | local | "" (use rfid_params.yaml)'),
        OpaqueFunction(function=_setup),
    ])

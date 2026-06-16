from setuptools import setup

package_name = 'rfid_node'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config',
         ['config/rfid_params.yaml', 'config/rfid_secrets.example.yaml']),
        ('share/' + package_name + '/launch', ['launch/rfid.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@todo.todo',
    description='RFID reader ROS nodes: serial driver + Sparkplug B / PackML bridge.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'rfid_driver_node = rfid_node.rfid_driver_node:main',
            'rfid_bridge_node = rfid_node.rfid_bridge_node:main',
        ],
    },
)

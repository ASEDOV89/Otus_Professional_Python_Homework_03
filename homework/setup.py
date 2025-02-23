from setuptools import setup, find_packages

setup(
    name='byterun',
    version='0.1',
    packages=find_packages(),
    install_requires=[
        'exceptiongroup==1.2.2',
        'mypy-extensions==1.0.0',
        'packaging==24.2',
        'pathspec==0.12.1',
        'platformdirs==4.3.6',
        'pluggy==1.5.0',
        'tomli==2.2.1',
        'typing_extensions==4.12.2',
    ],
    tests_require=[
        'pytest==8.3.4',
    ],
    extras_require={
        'dev': [
            'click==8.1.8',
            'iniconfig==2.0.0',
            'isort==6.0.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'byterun=byterun.__main__:main',
        ],
    },
)
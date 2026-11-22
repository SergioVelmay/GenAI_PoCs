# AWS Emulator oficial (checkpoint G→13.2). AWS no publica imagen: se construye con el paquete RPM,
# como indica su guía de Docker. Licencia CC0 1.0 (sin EULA).
# https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-docker.html
#
# Versión fijada: sale una release cada ~2 h y un parche puede cambiar respuestas. Subirla es un
# cambio deliberado (versiones publicadas: .../LTS/versions.json; fidelidad: releases de GitHub).
FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal

ARG AWS_EMULATOR_VERSION=1.0.569
# x86_64 (Windows y la CI). En Arm: aws-emulator-<versión>-1-aarch64.rpm.
ADD https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/aws-emulator-${AWS_EMULATOR_VERSION}-1-x86_64.rpm /tmp/aws-emulator.rpm
RUN rpm -i /tmp/aws-emulator.rpm && rm /tmp/aws-emulator.rpm \
    && mkdir -p /home/emulator/.aws/emulator && chown -R 1000 /home/emulator

USER 1000
# 0.0.0.0 dentro del contenedor para que Docker publique el puerto (en el host, solo 127.0.0.1).
# Telemetría apagada: por defecto envía datos de uso anónimos a AWS.
ENV HOME=/home/emulator \
    EMULATOR_BIND_ADDRESS=0.0.0.0 \
    EMULATOR_TELEMETRY=off
ENTRYPOINT ["aws-emulator"]

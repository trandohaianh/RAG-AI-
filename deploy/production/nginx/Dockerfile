FROM nginx:1.30.6-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
RUN apk add --no-cache logrotate
COPY --chmod=0644 logrotate.conf /etc/logrotate.d/ai-systems
COPY --chmod=0644 nginx.conf /templates/nginx.conf
COPY --chmod=0555 entrypoint.sh /usr/local/bin/gateway-entrypoint
STOPSIGNAL SIGQUIT
ENTRYPOINT ["/usr/local/bin/gateway-entrypoint"]
